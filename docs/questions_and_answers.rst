Questions & Answers
*******************

This document describes niche use cases that the Quick Start does not cover.
For typical usage patterns, consult the files in examples/ and the Quick Start,
in that order.

* **What physics does PLATON take into account?**

  We account for gas absorption, collisional absorption, an opaque
  cloud deck, and scattering with user-specified slope and amplitude
  (or Rayleigh, if not specified).  34 chemical species are included
  in our calculations, namely the ones listed in data/species_info.
  The abundances of these species were calculated using GGchem for a
  grid of metallicity, C/O ratio, temperature, and pressure, assuming
  equilibrium chemistry.  Metallicity ranges from 0.1-1000x solar, C/O
  ratio from 0.05 to 2, temperature from 300 to 3000 K, and pressure
  from 10^-4 to 10^8 Pa.  If you wander outside these limits, PLATON
  will throw a ValueError.
  
* **Why does PLATON not exactly agree with ExoTransmit?**

  We have made many improvements to the ExoTransmit algorithm to enhance the
  accuracy of our transit depth calculations.  Among the most consequential are
  allowing the gravitational acceleration to vary with height, and truncating
  the atmosphere at 10^-4 Pa instead of 0.1 Pa.  Both of these changes tend to
  increase the transit depth.  Precise agreement with
  ExoTransmit should not be expected.

* **How do I use PLATON with ExoTransmit input files? Or: how do I specify
  custom abundances and T/P profiles?**
  
  By example: ::
    
    from platon.abundance_getter import AbundanceGetter
    from platon.transit_depth_calculator import TransitDepthCalculator
    
    _, pressures, temperatures = np.loadtxt("t_p_1200K.dat", skiprows=1, unpack=True)

    # These files are found in examples/custom_abundances.  They are equivalent
    # to the ExoTransmit EOS files, except that COS is renamed to OCS
    abundances = AbundanceGetter.from_file("abund_1Xsolar_cond.dat")

    calculator = TransitDepthCalculator()
    wavelengths, transit_depths = calculator.compute_depths(star_radius, planet_mass, planet_radius, temperature=None, logZ=None, CO_ratio=None, custom_abundances=abundances, custom_T_profile=temperatures, custom_P_profile=pressures)

* **Should I use PLATON with ExoTransmit input files?**

  No.  The recommended usage is much simpler, and is outlined in both the
  examples/ directory and the Quick Start.

* **Which parameters are supported in retrieval?**
  See the documentation for :func:`~platon.retriever.Retriever.get_default_fit_info`.  All arguments to this method are possible fit parameters.  However, we
  recommend not fitting for T_star, as it has a very small effect on the result
  to begin with.  Mp and Rs are usually measured to greater precision than you
  can achieve in a fit, but we recommend fitting them with Gaussian priors to
  take into account the measurement errors.

* **Should I use run_multinest, or run_emcee?**
  
  That depends on whether you like nested sampling or MCMC!  You should try
  both and compare the results.  Nestled sampling is usually faster and has
  an automatically determined stopping point, so we recommend starting with
  that.  However, we have encountered cases where nested sampling stalls and
  does not finish even after many weeks.  In such cases, use MCMC.
   
* **My corner plots look ugly.  What do I do?**
  
  If you're using nested sampling, increase the number of live points. This
  will increase the number of samples your corner plot is generated from: ::

    # By default, npoints is 100
    result = retriever.run_multinest(bins, depths, errors, fit_info, npoints=1000)
    
  If you're using MCMC, increase nsteps from the default of 1000 to 10,000.

* **How do I get statistics from the retrieval?**

  Look at BestFit.txt.  It'll have the 16th, 50th, and 84th percentiles of
  all parameters, as well as the best fit values.
  
* **How do I do check what effect a species has on the transit spectrum?**
  You can tweak the atmospheric abundances and see what happens.  First, get
  baseline abundances: ::

    from platon.abundance_getter import AbundanceGetter
    getter = AbundanceGetter()
    # Solar logZ and C/O ratio. Modify as required.
    abundances = getter.get(0, 0.53)

  You can then modify this at will: ::

    # Zero out CO.  (Note that if CO is a major component, you should probably
    # renormalize the abundances of other species so that they add up to 1.)
    
    abundances["CO"] *= 0

    # Set CH4 abundance to a constant throughout the atmosphere
    abundances["CH4"] *= 0
    abundances["CH4"] += 1e-5

  Then call compute_depths with logZ and CO_ratio set to None: ::

    calculator.compute_depths(star_radius, planet_mass, planet_radius, temperature, logZ=None, CO_ratio=None, custom_abundances=abundances)

* **How do I specify custom abundances in the forward model?**
  See the answer to the above question.  If you want to specify different
  abundances for each temperature/pressure point instead of a constant
  abundance, see the documentation for custom_abundances in :func:`~platon.transit_depth_calculator.TransitDepthCalculator.compute_depths`
    
* **How do I retrieve individual species abundances?**
  You can't.  While this would be trivial to implement--and you can do so if
  you really need to--it could easily lead to combinations of species
  that are unstable on very short timescales.  We have therefore decided not
  to support retrieving on individual abundances.
  
* **PLATON is still too slow!  How do I make it faster?**

  If you didn't follow the installation instructions, go back and re-read them.
  Make sure you have OpenBLAS, MKL, or another basic linear algebra library
  (BLAS) installed
  and linked to numpy.  If you intend to use the eclipse depth calculator and
  have a CUDA-capable GPU, install CUDA, cudamat, and gnumpy; the eclipse
  depth calculator should detect gnumpy and use the GPU.

  If PLATON is still too slow, try decreasing num_profile_heights in
  transit_depth_calculator.py (for transit depths) or
  TP_profile (for eclipse depths).  Of course, this comes at the expense of
  accuracy.  You can also delete some of the files in data/Absorption that
  correspond to molecules which contribute negligible opacity.  This has the
  effect of setting their absorption cross section to 0.
  
  We have experienced cases where the nested sampling retrieval seemingly gets
  stuck, and doesn't finish even after many weeks.  In these cases, try MCMC
  instead, as that is guaranteed to terminate in the number of steps you
  specify.
  
