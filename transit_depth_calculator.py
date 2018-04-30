from species_data_reader import read_species_data
import interpolator_3D
import eos_reader
from scipy.interpolate import RectBivariateSpline
from tau_los import get_line_of_sight_tau
import numpy as np
import matplotlib.pyplot as plt
import time
import pickle
from constants import k_B, amu

class TransitDepthCalculator:
    def __init__(self, planet_radius, star_radius, g, absorption_dir="Absorption", species_info_file="species_info", lambda_grid_file="wavelengths.npy", P_grid_file="pressures.npy", T_grid_file="temperatures.npy", collisional_absorption_file="collisional_absorption.pkl"):
        self.planet_radius = planet_radius
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
            
    def __rebin_absorption_dict__(self, bins, dictionary):
        new_dict = dict()
        
        for key in dictionary:
            shape = dictionary[key].shape
            assert(dictionary[key].shape[0] == self.N_lambda)

            new_shape = list(shape)
            new_shape[0] = len(bins)
            
            new_dict[key] = np.zeros(new_shape)           
            for i, (start, end) in enumerate(bins):
                selection = np.logical_and(self.lambda_grid >= start, self.lambda_grid < end)
                averaged = np.mean(dictionary[key][selection], axis=0)
                new_dict[key][i] = averaged

        return new_dict
        
    def change_wavelength_bins(self, bins):
        if self.wavelength_rebinned:
            raise NotImplementedError("Multiple re-binnings not yet supported")

        self.wavelength_rebinned = True
        
        bins = np.array(bins)
        if bins.ndim == 1:
            new_bins = []
            for i in range(len(bins)-1):
                new_bins.append([bins[i], bins[i+1]])
        bins = np.array(new_bins)
        
        self.absorption_data = self.__rebin_absorption_dict__(bins, self.absorption_data)
        self.collisional_absorption_data = self.__rebin_absorption_dict__(bins, self.collisional_absorption_data)
        self.lambda_grid = np.array([np.mean([start, end]) for (start, end) in bins])
        self.N_lambda = len(bins)
        
        P_meshgrid, lambda_meshgrid, T_meshgrid = np.meshgrid(self.P_grid, self.lambda_grid, self.T_grid)
        self.P_meshgrid = P_meshgrid
        self.T_meshgrid = T_meshgrid
        
        
    def get_gas_absorption(self, abundances):
        absorption_coeff = np.zeros((self.N_lambda, self.N_P, self.N_T))
         
        for species_name in abundances.keys():
            assert(abundances[species_name].shape == (self.N_P, self.N_T))
            if species_name in self.absorption_data: 
                absorption_coeff += self.absorption_data[species_name] * abundances[species_name]
                
        return absorption_coeff

        
    def get_scattering_absorption(self, abundances):
        cross_section = np.zeros((self.N_lambda, self.N_P, self.N_T))
        scatt_prefactor = 8*np.pi/3 * (2*np.pi/self.lambda_grid)**4
        scatt_prefactor = scatt_prefactor.reshape((self.N_lambda,1,1))
        
        for species_name in abundances:
            if species_name in self.polarizability_data:
                cross_section += abundances[species_name] * self.polarizability_data[species_name]**2 * scatt_prefactor

        return cross_section * self.P_meshgrid/(k_B*self.T_meshgrid)

    
    def get_collisional_absorption(self, abundances):
        absorption_coeff = np.zeros((self.N_lambda, self.N_P, self.N_T))
        n = self.P_meshgrid/(k_B * self.T_meshgrid)                    
        
        for s1, s2 in self.collisional_absorption_data:
            if s1 in abundances and s2 in abundances:
                n1 = abundances[s1]*n
                n2 = abundances[s2]*n
                abs_data = self.collisional_absorption_data[(s1,s2)].reshape((self.N_lambda, 1, self.N_T))
                absorption_coeff += abs_data * n1 * n2

        return absorption_coeff
        

    def compute_depths(self, P, T, abundances, add_scattering=True, add_collisional_absorption=True, cloudtop_pressure=np.inf):
        '''
        P: List of pressures in atmospheric P-T profile, in ascending order
        T: List of temperatures corresponding to pressures in P
        abundances: dictionary mapping species name to (N_T, N_P) array, where N_T is the number of temperature points in the absorption data files, and N_P is the number of pressure points in those files
        add_scattering: whether Rayleigh scattering opacity is taken into account
        add_collisional_absorption: whether collisionally induced absorption is taken into account
        cloudtop_pressure: pressure level below which light cannot penetrate'''
        start = time.time()
        assert(len(P) == len(T))

        absorption_coeff = self.get_gas_absorption(abundances)
        if add_scattering: absorption_coeff += self.get_scattering_absorption(abundances)
        if add_collisional_absorption: absorption_coeff += self.get_collisional_absorption(abundances)
        
        mu = np.zeros(len(P))
        for species_name in abundances:
            interpolator = RectBivariateSpline(self.P_grid, self.T_grid, abundances[species_name], kx=1, ky=1)
            atm_abundances = interpolator.ev(P, T)
            mu += atm_abundances * self.mass_data[species_name]

        absorption_coeff_atm = interpolator_3D.fast_interpolate(absorption_coeff, self.T_grid, self.P_grid, T, P)

        dP = P[1:] - P[0:-1]
        dh = dP/P[1:] * k_B * T[1:]/(mu[1:] * amu* self.g)
        dh = np.append(k_B*T[0]/(mu[0] * amu * self.g), dh)
        
        #dz goes from top to bottom of atmosphere
        radius_with_atm = np.sum(dh) + self.planet_radius
        heights = np.append(radius_with_atm, radius_with_atm - np.cumsum(dh))
        tau_los = get_line_of_sight_tau(absorption_coeff_atm, heights)

        absorption_fraction = 1 - np.exp(-tau_los)
        
        absorption_fraction[:, P > cloudtop_pressure] = 0
        
        transit_depths = (self.planet_radius/self.star_radius)**2 + 2/self.star_radius**2 * absorption_fraction.dot(heights[1:] * dh)

        end = time.time()
        print "Time taken", end-start
        return transit_depths
        

index, P, T = np.loadtxt("T_P/t_p_800K.dat", unpack=True, skiprows=1)
abundances = eos_reader.get_abundances("EOS/eos_1Xsolar_cond.dat")
    
depth_calculator = TransitDepthCalculator(6.4e6, 7e8, 9.8)
#depth_calculator.change_wavelength_bins([[1e-6,2e-6], [2e-6,3e-6]])
#depth_calculator.change_wavelength_bins(np.linspace(1.1e-6, 1.7e-6, 30))

transit_depths = depth_calculator.compute_depths(P, T, abundances, cloudtop_pressure=np.inf)
transit_depths *= 100

#plt.plot(depth_calculator.lambda_grid, transit_depths)
#plt.show()

ref_wavelengths, ref_depths = np.loadtxt("ref_spectra.dat", unpack=True, skiprows=2)
plt.plot(ref_wavelengths, ref_depths, label="ExoTransmit")
plt.plot(depth_calculator.lambda_grid, transit_depths, label="PyExoTransmit")
plt.legend()
plt.figure()
plt.title("Residuals of comparison")
plt.plot(ref_wavelengths, ref_depths-transit_depths)

plt.figure()

depth_calculator.change_wavelength_bins(np.linspace(1.1e-6, 1.7e-6, 30))
binned_transit_depths = depth_calculator.compute_depths(P, T, abundances) * 100
plt.plot(ref_wavelengths, transit_depths, label="Unbinned")
plt.plot(depth_calculator.lambda_grid, binned_transit_depths, label="Binned")

plt.show()

