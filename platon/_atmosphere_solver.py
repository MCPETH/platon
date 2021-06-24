import os
import sys

from pkg_resources import resource_filename
from scipy.interpolate import RectBivariateSpline, UnivariateSpline, RegularGridInterpolator
import numpy as np
import matplotlib.pyplot as plt
from scipy import integrate
import scipy.interpolate
import scipy.ndimage
from scipy.stats import lognorm

from . import _hydrostatic_solver
from ._loader import load_dict_from_pickle, load_numpy
from .abundance_getter import AbundanceGetter
from ._species_data_reader import read_species_data
from . import _interpolator_3D
from ._tau_calculator import get_line_of_sight_tau
from .constants import k_B, AMU, M_sun, Teff_sun, G, h, c
from ._get_data import get_data_if_needed
from ._mie_cache import MieCache
from .errors import AtmosphereError

class AtmosphereSolver:
    def __init__(self, include_condensation=True, num_profile_heights=250,
                 ref_pressure=1e5, method='xsec'):        
        self.arguments = locals()
        del self.arguments["self"]

        get_data_if_needed()
        
        self.absorption_data, self.mass_data, self.polarizability_data = read_species_data(
            resource_filename(__name__, "data/Absorption"),
            resource_filename(__name__, "data/species_info"),
            method)

        self.low_res_lambdas = load_numpy("data/low_res_lambdas.npy")

        if method == "xsec":
            self.lambda_grid = load_numpy("data/wavelengths.npy")
            self.d_ln_lambda = np.median(np.diff(np.log(self.lambda_grid)))
        else:
            self.lambda_grid = load_numpy("data/k_wavelengths.npy")
            self.d_ln_lambda = np.median(np.diff(np.log(np.unique(self.lambda_grid))))

        self.collisional_absorption_data = load_dict_from_pickle(
            "data/collisional_absorption.pkl") 
        for key in self.collisional_absorption_data:
            self.collisional_absorption_data[key] = scipy.interpolate.interp1d(
                self.low_res_lambdas,
                self.collisional_absorption_data[key])(self.lambda_grid)
            
        self.P_grid = load_numpy("data/pressures.npy")
        self.T_grid = load_numpy("data/temperatures.npy")

        self.N_lambda = len(self.lambda_grid)
        self.N_T = len(self.T_grid)
        self.N_P = len(self.P_grid)

        self.wavelength_rebinned = False
        self.wavelength_bins = None

        self.abundance_getter = AbundanceGetter(include_condensation)
        self.min_temperature = max(np.min(self.T_grid), self.abundance_getter.min_temperature)
        self.max_temperature = np.max(self.T_grid)

        self.num_profile_heights = num_profile_heights
        self.ref_pressure = ref_pressure
        self.method = method
        self._mie_cache = MieCache()

        self.all_cross_secs = load_dict_from_pickle("data/all_cross_secs.pkl")
        self.all_radii = load_numpy("data/mie_radii.npy")


    def change_wavelength_bins(self, bins):
        """Specify wavelength bins, instead of using the full wavelength grid
        in self.lambda_grid.  This makes the code much faster, as
        `compute_depths` will only compute depths at wavelengths that fall
        within a bin.

        Parameters
        ----------
        bins : array_like, shape (N,2)
            Wavelength bins, where bins[i][0] is the start wavelength and
            bins[i][1] is the end wavelength for bin i. If bins is None, resets
            the calculator to its unbinned state.

        Raises
        ------
        NotImplementedError
            Raised when `change_wavelength_bins` is called more than once,
            which is not supported.
        """
        if self.wavelength_rebinned:
            self.__init__(**self.arguments)
            self.wavelength_rebinned = False        
            
        if bins is None:
            return

        for start, end in bins:
            if start < np.min(self.lambda_grid) \
               or start > np.max(self.lambda_grid) \
               or end < np.min(self.lambda_grid) \
               or end > np.max(self.lambda_grid):
                raise ValueError("Invalid wavelength bin: {}-{} meters".format(start, end))
            num_points = np.sum(np.logical_and(self.lambda_grid > start,
                                               self.lambda_grid < end))
            if num_points == 0:
                raise ValueError("Wavelength bin too narrow: {}-{} meters".format(start, end))
            if num_points <= 5:
                print("WARNING: only {} points in {}-{} m bin. Results will be inaccurate".format(num_points, start, end))
        
        self.wavelength_rebinned = True
        self.wavelength_bins = bins

        cond = np.any([
            np.logical_and(self.lambda_grid > start, self.lambda_grid < end) \
            for (start, end) in bins], axis=0)

        for key in self.absorption_data:
            self.absorption_data[key] = self.absorption_data[key][:, :, cond]

        self.lambda_grid = self.lambda_grid[cond]
        self.N_lambda = len(self.lambda_grid)

        for key in self.collisional_absorption_data:
            self.collisional_absorption_data[key] = self.collisional_absorption_data[key][:, cond]
  
    def _get_k(self, T, wavelengths):
        wavelengths = 1e6 * np.copy(wavelengths)
        alpha = 14391
        lambda_0 = 1.6419

        #Calculate bound-free absorption coefficient
        k_bf = np.zeros(len(wavelengths))
        cond = wavelengths < lambda_0
        C = [152.519, 49.534, -118.858, 92.536, -34.194, 4.982]
        f_lambda = np.sum([C[i-1] * (1/wavelengths[cond] - 1/lambda_0)**((i-1)/2) for i in range(1,7)], axis=0)
        sigma = 1e-18 * wavelengths[cond]**3 * (1 / wavelengths[cond] - 1 / lambda_0)**1.5 * f_lambda
        k_bf[cond] = 0.75 * T**-2.5 * np.exp(alpha/lambda_0 / T) * (1 - np.exp(-alpha / wavelengths[cond] / T)) * sigma

        #Now calculate free-free absorption coefficient
        k_ff = np.zeros(len(wavelengths))
        mid = np.logical_and(wavelengths > 0.1823, wavelengths < 0.3645)
        red = wavelengths > 0.3645
                    
        ff_matrix_red = np.array([
            [0, 0, 0, 0, 0, 0],
            [2483.346, 285.827, -2054.291, 2827.776, -1341.537, 208.952],
            [-3449.889, -1158.382, 8746.523, -11485.632, 5303.609, -812.939],
            [2200.04, 2427.719, -13651.105, 16755.524, -7510.494, 1132.738],
            [-696.271, -1841.4, 8624.97, -10051.53, 4400.067, -655.02],
            [88.283, 444.517, -1863.864, 2095.288, -901.788, 132.985]])
        ff_matrix_mid = np.array([
            [518.1021, -734.8666, 1021.1775, -479.0721, 93.1373, -6.4285],
            [473.2636, 1443.4137, -1977.3395, 922.3575, -178.9275, 12.36],
            [-482.2089, -737.1616, 1096.8827, -521.1341, 101.7963, -7.0571],
            [115.5291, 169.6374, -245.649, 114.243, -21.9972, 1.5097],
            [0, 0, 0, 0, 0, 0],
            [0, 0, 0, 0, 0, 0]])

        for n in range(1, 7):
            A_mid = np.array([wavelengths[mid]**i for i in (2, 0, -1, -2, -3, -4)]).T
            #print(A_mid.shape)
            A_red = np.array([wavelengths[red]**i for i in (2, 0, -1, -2, -3, -4)]).T
            
            k_ff[mid] += 1e-29 * (5040/T)**((n+1)/2) * A_mid.dot(ff_matrix_mid[n-1]) #np.sum(ff_matrix_mid[n-1] * np.array([wavelength**2, 1, wavelength**-1, wavelength**-2, wavelength**-3, wavelength**-4]))
            k_ff[red] += 1e-29 * (5040/T)**((n+1)/2) * A_red.dot(ff_matrix_red[n-1])

        k = k_bf + k_ff
        
        #1e-4 to convert from cm^4/dyne to m^4/N
        return k * 1e-4    

    def _get_H_minus_absorption(self, abundances, P_cond, T_cond):
        absorption_coeff = np.zeros(
            (np.sum(T_cond), np.sum(P_cond), self.N_lambda))
        
        valid_Ts = self.T_grid[T_cond]
        trunc_el_abundances = abundances["el"][T_cond][:, P_cond]
        trunc_H_abundances = abundances["H"][T_cond][:, P_cond]
        
        for t in range(len(valid_Ts)):
            k = self._get_k(valid_Ts[t], self.lambda_grid)          
            absorption_coeff[t] = k * (trunc_el_abundances[t] * trunc_H_abundances[t] * self.P_grid[P_cond]**2)[:, np.newaxis] / (k_B * valid_Ts[t])
                  
        return absorption_coeff


    def _get_gas_absorption(self, atm_abundances, P_profile, T_profile,
                            min_absorption=1e-99):
        absorption_coeff = 0.
        
        for species_name, species_abundance in atm_abundances.items():
            if species_name in self.absorption_data:
                cond = self.absorption_data[species_name] < min_absorption
                self.absorption_data[species_name][cond] = min_absorption
                absorption_coeff += species_abundance[:, np.newaxis] * 10.0 ** scipy.interpolate.interpn(
                    (self.T_grid, np.log10(self.P_grid)),
                    np.log10(self.absorption_data[species_name]),
                    np.array([T_profile, np.log10(P_profile)]).T)
                        
        return absorption_coeff 

    def _get_scattering_absorption(self, atm_abundances, P_profile, T_profile,
                                   multiple=1, slope=4, ref_wavelength=1e-6):
        sum_polarizability_sqr = np.zeros(len(P_profile)) 

        for species_name in atm_abundances:
            if species_name in self.polarizability_data:
                sum_polarizability_sqr += atm_abundances[species_name] * self.polarizability_data[species_name]**2

        n = P_profile / (k_B * T_profile)
        result = (multiple * (128.0 / 3 * np.pi**5) * ref_wavelength**(slope - 4) * n * sum_polarizability_sqr)[:, np.newaxis] / self.lambda_grid**slope
        return result

    def _get_collisional_absorption(self, atm_abundances, P_profile, T_profile, min_absorption=1e-99):
        absorption_coeff = np.zeros(
            (len(P_profile), self.N_lambda))
        n = P_profile / (k_B * T_profile)

        for s1, s2 in self.collisional_absorption_data:
            if s1 in atm_abundances and s2 in atm_abundances:
                cond = self.collisional_absorption_data[(s1, s2)] < min_absorption
                self.collisional_absorption_data[(s1, s2)][cond] = min_absorption
                n1 = (atm_abundances[s1] * n)
                n2 = (atm_abundances[s2] * n)

                abs_data = 10.00 ** scipy.interpolate.interp1d(
                    self.T_grid,
                    np.log10(self.collisional_absorption_data[(s1,s2)]), axis=0)(T_profile)
                
                #abs_data = 10.0 ** scipy.interpolate.interpn(
                #    [self.T_grid],
                #    np.log10(self.collisional_absorption_data[(s1, s2)]),
                #    T_profile)
                #    #np.array([T_profile]).T)
                        
                absorption_coeff += abs_data * (n1 * n2)[:, np.newaxis]

        return absorption_coeff
       
    def _get_above_cloud_profiles(self, P_profile, T_profile, abundances,
                                  planet_mass, planet_radius,
                                  above_cloud_cond):
        
        assert(len(P_profile) == len(T_profile))
        # First, get atmospheric weight profile
        mu_profile = np.zeros(len(P_profile))
        atm_abundances = {}
        
        for species_name in abundances:
            interpolator = RectBivariateSpline(
                self.T_grid, np.log10(self.P_grid),
                np.log10(abundances[species_name]), kx=1, ky=1)
            abund = 10**interpolator.ev(T_profile, np.log10(P_profile))
            atm_abundances[species_name] = abund
            mu_profile += abund * self.mass_data[species_name]

        radii, dr = _hydrostatic_solver._solve(
            P_profile, T_profile, self.ref_pressure, mu_profile, planet_mass,
            planet_radius, above_cloud_cond)
        
        for key in atm_abundances:
            atm_abundances[key] = atm_abundances[key][above_cloud_cond]
            
        return radii, dr, atm_abundances, mu_profile

    def _get_abundances_array(self, logZ, CO_ratio, custom_abundances):
        if custom_abundances is None:
            return self.abundance_getter.get(logZ, CO_ratio)

        if logZ is not None or CO_ratio is not None:
            raise ValueError(
                "Must set logZ=None and CO_ratio=None to use custom_abundances")

        if isinstance(custom_abundances, str):
            # Interpret as filename
            return AbundanceGetter.from_file(custom_abundances)

        if isinstance(custom_abundances, dict):
            for key, value in custom_abundances.items():
                if not isinstance(value, np.ndarray):
                    raise ValueError(
                        "custom_abundances must map species names to arrays")
                if value.shape != (self.N_T, self.N_P):
                    raise ValueError(
                        "custom_abundances has array of invalid size")
            return custom_abundances

        raise ValueError("Unrecognized format for custom_abundances")
   

    def _validate_params(self, T_profile, logZ, CO_ratio, cloudtop_pressure):
        if np.min(T_profile) < self.min_temperature or\
           np.max(T_profile) > self.max_temperature:
            raise AtmosphereError("Invalid temperatures in T/P profile")
            
        if logZ is not None:
            minimum = np.min(self.abundance_getter.logZs)
            maximum = np.max(self.abundance_getter.logZs)
            if logZ < minimum or logZ > maximum:
                raise ValueError(
                    "logZ {} is out of bounds ({} to {})".format(
                        logZ, minimum, maximum))

        if CO_ratio is not None:
            minimum = np.min(self.abundance_getter.CO_ratios)
            maximum = np.max(self.abundance_getter.CO_ratios)
            if CO_ratio < minimum or CO_ratio > maximum:
                raise ValueError(
                    "C/O ratio {} is out of bounds ({} to {})".format(CO_ratio, minimum, maximum))

        if not np.isinf(cloudtop_pressure):
            minimum = np.min(self.P_grid)
            maximum = np.max(self.P_grid)
            if cloudtop_pressure <= minimum or cloudtop_pressure > maximum:
                raise ValueError(
                    "Cloudtop pressure is {} Pa, but must be between {} and {} Pa unless it is np.inf".format(
                        cloudtop_pressure, minimum, maximum))
            
            
    def compute_params(self, planet_mass, planet_radius,
                       P_profile, T_profile,
                       logZ=0, CO_ratio=0.53,
                       add_gas_absorption=True,
                       add_H_minus_absorption=False,
                       add_scattering=True, scattering_factor=1,
                       scattering_slope=4, scattering_ref_wavelength=1e-6,
                       add_collisional_absorption=True,
                       cloudtop_pressure=np.inf, custom_abundances=None,
                       ri=None, frac_scale_height=1, number_density=0,
                       part_size=1e-6, part_size_std=0.5,
                       P_quench=1e-99,
                       min_abundance=1e-99, min_cross_sec=1e-99):

        self._validate_params(T_profile, logZ, CO_ratio, cloudtop_pressure)
        abundances = self._get_abundances_array(
            logZ, CO_ratio, custom_abundances)
 
        T_quench = np.interp(np.log(P_quench), np.log(P_profile), T_profile)   
     
        for name in abundances:
            abundances[name][np.isnan(abundances[name])] = min_abundance
            abundances[name][abundances[name] < min_abundance] = min_abundance
                        
        above_clouds = P_profile < cloudtop_pressure
        radii, dr, atm_abundances, mu_profile = self._get_above_cloud_profiles(
            P_profile, T_profile, abundances, planet_mass, planet_radius,
            above_clouds)
            
        for name in atm_abundances:
            quench_abund = np.exp(np.interp(
                np.log(P_quench),
                np.log(P_profile),
                np.log(atm_abundances[name])))
            atm_abundances[name][P_profile < P_quench] = quench_abund
      
        P_profile = P_profile[above_clouds]
        T_profile = T_profile[above_clouds]

        T_cond = _interpolator_3D.get_condition_array(T_profile, self.T_grid)
        P_cond = _interpolator_3D.get_condition_array(
            P_profile, self.P_grid, cloudtop_pressure)
        absorption_coeff = np.zeros((len(P_profile), len(self.lambda_grid)))

        if add_gas_absorption:
            absorption_coeff += self._get_gas_absorption(atm_abundances, P_profile, T_profile)
        if add_H_minus_absorption:
            raise ValueError("Not implemented")
            absorption_coeff += self._get_H_minus_absorption(abundances, P_cond, T_cond)
        if add_scattering:
            if ri is not None:
                raise ValueError("Not implemented")
            else:
                absorption_coeff += self._get_scattering_absorption(
                    atm_abundances, P_profile, T_profile,
                    scattering_factor, scattering_slope,
                    scattering_ref_wavelength)

        if add_collisional_absorption:
            absorption_coeff += self._get_collisional_absorption(
                atm_abundances, P_profile, T_profile)


        cross_secs_atm = absorption_coeff / (P_profile / k_B / T_profile)[:, np.newaxis]        
        absorption_coeff_atm = cross_secs_atm * (P_profile / k_B / T_profile)[:, np.newaxis]
        output_dict = {"absorption_coeff_atm": absorption_coeff_atm,
                       "radii": radii,
                       "dr": dr,
                       "P_profile": P_profile,
                       "T_profile": T_profile,
                       "mu_profile": mu_profile,
                       "atm_abundances": atm_abundances,
                       "unbinned_wavelengths": self.lambda_grid}
        
        return output_dict
