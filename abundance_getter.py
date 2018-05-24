import numpy as np
import os
import eos_reader
import scipy
import pickle

class AbundanceGetter:
    def __init__(self, format='ggchem', include_condensates=False):
        self.metallicities = None
        self.abundances = None
        self.min_temperature = None
        
        if format == 'ggchem':
            self.load_ggchem_files(include_condensates)
        elif format == 'exotransmit':
            self.load_exotransmit_files(include_condensates)
        else:
            assert(False)
            
    def load_exotransmit_files(self, include_condensates):
        self.min_temperature = 100
        self.metallicities = [0.1, 1, 5, 10, 30, 50, 100, 1000]
        self.abundances = []

        if include_condensates:
            suffix = "cond"
        else:
            suffix = "gas"
        
        for m in self.metallicities:
            m_str = str(m).replace('.', 'p')
            filename = "EOS/eos_{0}Xsolar_{1}.dat".format(m_str, suffix)
            self.abundances.append(eos_reader.get_abundances(filename))

    def load_ggchem_files(self, include_condensates):
        all_logZ = np.linspace(-1, 3, 81)
        self.metallicities = 10**all_logZ
        self.abundances = []

        if include_condensates:
            sub_dir = "cond"
            self.min_temperature = 300
        else:
            sub_dir = "gas_only"
            self.min_temperature = 100
            
        file_exists = np.ones(len(all_logZ), dtype=bool)
        
        for i,logZ in enumerate(all_logZ):
            filename = "abundances/{0}/abund_dict_{1}.pkl".format(sub_dir, str(logZ))
            if not os.path.isfile(filename):
                file_exists[i] = False
                continue

            with open(filename) as f:                
                self.abundances.append(pickle.load(f))
            
        self.metallicities = self.metallicities[file_exists]

    def interp(self, metallicity):
        result = dict()
        for key in self.abundances[0]:
            grids = [self.abundances[i][key] for i in range(len(self.abundances))]
            interpolator = scipy.interpolate.interp1d(self.metallicities, grids, axis=0)
            result[key] = interpolator(metallicity)
        return result

    def get_metallicity_bounds(self):
        return np.min(self.metallicities), np.max(self.metallicities)

    def get_min_temperature(self):
        return self.min_temperature
