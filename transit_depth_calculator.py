from species_data_reader import read_species_data
import interpolator_3D
import eos_reader
from scipy.interpolate import RectBivariateSpline
from tau_calculator import get_line_of_sight_tau
import numpy as np
import matplotlib.pyplot as plt
import time
import pickle
from constants import k_B, amu

class TransitDepthCalculator:
    def __init__(self, star_radius, g, absorption_dir="Absorption", species_info_file="species_info", lambda_grid_file="wavelengths.npy", P_grid_file="pressures.npy", T_grid_file="temperatures.npy", collisional_absorption_file="collisional_absorption.pkl"):
        self.star_radius = star_radius
        self.g = g
        self.absorption_data, self.mass_data, self.polarizability_data = read_species_data(absorption_dir, species_info_file)

        self.collisional_absorption_data = np.load(collisional_absorption_file)

        self.lambda_grid = np.load(lambda_grid_file)
        self.P_grid = np.load(P_grid_file)
        self.T_grid = np.load(T_grid_file)

        self.N_lambda = len(self.lambda_grid)
        self.N_T = len(self.T_grid)
        self.N_P = len(self.P_grid)

        P_meshgrid, lambda_meshgrid, T_meshgrid = np.meshgrid(self.P_grid, self.lambda_grid, self.T_grid)
        self.P_meshgrid = P_meshgrid
        self.T_meshgrid = T_meshgrid

        self.wavelength_rebinned = False
        self.wavelength_bins = None
        
   
    def change_wavelength_bins(self, bins):
        if self.wavelength_rebinned:
            raise NotImplementedError("Multiple re-binnings not yet supported")

        self.wavelength_rebinned = True
        self.wavelength_bins = bins

        cond = np.any([np.logical_and(self.lambda_grid > start, self.lambda_grid < end) for (start,end) in bins], axis=0)

        for key in self.absorption_data:
            self.absorption_data[key] = self.absorption_data[key][cond]

        for key in self.collisional_absorption_data:
            self.collisional_absorption_data[key] = self.collisional_absorption_data[key][cond]
        
        self.lambda_grid = self.lambda_grid[cond]
        self.N_lambda = len(self.lambda_grid)
        
        P_meshgrid, lambda_meshgrid, T_meshgrid = np.meshgrid(self.P_grid, self.lambda_grid, self.T_grid)
        self.P_meshgrid = P_meshgrid
        self.T_meshgrid = T_meshgrid
        
        
    def get_gas_absorption(self, abundances, P_cond, T_cond):
        absorption_coeff = np.zeros((self.N_lambda, np.sum(P_cond), np.sum(T_cond)))         
        for species_name in abundances.keys():
            assert(abundances[species_name].shape == (self.N_P, self.N_T))
            if species_name in self.absorption_data:
                absorption_coeff += self.absorption_data[species_name][:,P_cond,:][:,:,T_cond] * abundances[species_name][P_cond,:][:,T_cond]
                
        return absorption_coeff

        
    def get_scattering_absorption(self, abundances, P_cond, T_cond):
        cross_section = np.zeros((self.N_lambda, np.sum(P_cond), np.sum(T_cond)))
        scatt_prefactor = 8*np.pi/3 * (2*np.pi/self.lambda_grid)**4
        scatt_prefactor = scatt_prefactor.reshape((self.N_lambda,1,1))
        
        for species_name in abundances:
            if species_name in self.polarizability_data:
                cross_section += abundances[species_name][P_cond,:][:,T_cond] * self.polarizability_data[species_name]**2 * scatt_prefactor
        
        return cross_section * self.P_meshgrid[:,P_cond,:][:,:,T_cond]/(k_B*self.T_meshgrid[:,P_cond,:][:,:,T_cond])

    
    def get_collisional_absorption(self, abundances, P_cond, T_cond):
        absorption_coeff = np.zeros((self.N_lambda, np.sum(P_cond), np.sum(T_cond)))
        n = self.P_meshgrid[:,P_cond,:][:,:,T_cond]/(k_B * self.T_meshgrid[:,P_cond,:][:,:,T_cond])
        
        for s1, s2 in self.collisional_absorption_data:
            if s1 in abundances and s2 in abundances:
                n1 = (abundances[s1][P_cond, :][:,T_cond]*n)
                n2 = (abundances[s2][P_cond, :][:,T_cond]*n)
                abs_data = self.collisional_absorption_data[(s1,s2)].reshape((self.N_lambda, 1, self.N_T))[:,:,T_cond]
                absorption_coeff += abs_data * n1 * n2

        return absorption_coeff
    

    def get_above_cloud_r_and_dr(self, P, T, abundances, planet_radius, P_cond):
        mu = np.zeros(len(P))
        for species_name in abundances:
            interpolator = RectBivariateSpline(self.P_grid, self.T_grid, abundances[species_name], kx=1, ky=1)
            atm_abundances = interpolator.ev(P, T)
            mu += atm_abundances * self.mass_data[species_name]

        dP = P[1:] - P[0:-1]
        dr = dP/P[1:] * k_B * T[1:]/(mu[1:] * amu* self.g)
        dr = np.append(k_B*T[0]/(mu[0] * amu * self.g), dr)
        
        #dz goes from top to bottom of atmosphere
        radius_with_atm = np.sum(dr) + planet_radius
        radii = radius_with_atm - np.cumsum(dr)
        radii = np.append(radius_with_atm, radii[P_cond])
        return radii, dr
    
    def compute_depths(self, planet_radius, P, T, abundances, add_scattering=True, scattering_factor=1, add_collisional_absorption=True, cloudtop_pressure=np.inf):
        '''
        P: List of pressures in atmospheric P-T profile, in ascending order
        T: List of temperatures corresponding to pressures in P
        abundances: dictionary mapping species name to (N_T, N_P) array, where N_T is the number of temperature points in the absorption data files, and N_P is the number of pressure points in those files
        add_scattering: whether Rayleigh scattering opacity is taken into account
        add_collisional_absorption: whether collisionally induced absorption is taken into account
        cloudtop_pressure: pressure level below which light cannot penetrate'''
        start = time.time()
        assert(len(P) == len(T))

        above_clouds = P < cloudtop_pressure
        radii, dr = self.get_above_cloud_r_and_dr(P, T, abundances, planet_radius, above_clouds)
        P = P[above_clouds]
        T = T[above_clouds]
        dr = dr[above_clouds]
        
        T_cond = interpolator_3D.get_condition_array(T, self.T_grid)
        P_cond = interpolator_3D.get_condition_array(P, self.P_grid, cloudtop_pressure)
     
        absorption_coeff = self.get_gas_absorption(abundances, P_cond, T_cond)
        if add_scattering: absorption_coeff += scattering_factor * self.get_scattering_absorption(abundances, P_cond, T_cond)
        if add_collisional_absorption: absorption_coeff += self.get_collisional_absorption(abundances, P_cond, T_cond)    

        absorption_coeff_atm = interpolator_3D.fast_interpolate(absorption_coeff, self.T_grid[T_cond], self.P_grid[P_cond], T, P)

        tau_los = get_line_of_sight_tau(absorption_coeff_atm, radii)

        absorption_fraction = 1 - np.exp(-tau_los)
        
        #absorption_fraction[:, P > cloudtop_pressure] = 0
        
        transit_depths = (planet_radius/self.star_radius)**2 + 2/self.star_radius**2 * absorption_fraction.dot(radii[1:] * dr)
        end = time.time()
        #print "Time taken", end-start
        
        binned_wavelengths = []
        binned_depths = []
        if self.wavelength_bins is not None:
            for (start, end) in self.wavelength_bins:
                cond = np.logical_and(self.lambda_grid >= start, self.lambda_grid < end)
                binned_wavelengths.append(np.mean(self.lambda_grid[cond]))
                binned_depths.append(np.mean(transit_depths[cond]))
            return np.array(binned_wavelengths), np.array(binned_depths)
        
        return self.lambda_grid, transit_depths
        

'''index, P, T = np.loadtxt("T_P/t_p_800K.dat", unpack=True, skiprows=1)
T = T*0.9
abundances = eos_reader.get_abundances("EOS/eos_1Xsolar_cond.dat")
    
depth_calculator = TransitDepthCalculator(7e8, 9.8)
wfc_wavelengths = np.linspace(1.1e-6, 1.7e-6, 30)
wavelength_bins = []
for i in range(len(wfc_wavelengths) - 1):
    wavelength_bins.append([wfc_wavelengths[i], wfc_wavelengths[i+1]])

wavelength_bins.append([3.2e-6, 4e-6])
wavelength_bins.append([4e-6, 5e-6])
depth_calculator.change_wavelength_bins(wavelength_bins)

wavelengths, transit_depths = depth_calculator.compute_depths(6.4e6, P, T, abundances, cloudtop_pressure=10)
print transit_depths
transit_depths *= 100

ref_wavelengths, ref_depths = np.loadtxt("ref_spectra.dat", unpack=True, skiprows=2)
plt.plot(ref_wavelengths, ref_depths, label="ExoTransmit")
plt.plot(wavelengths, transit_depths, label="PyExoTransmit")
plt.legend()
plt.show()'''

