Install
*******

Before installing PLATON, it is highly recommended to have a fast linear
algebra library (BLAS) and verify that numpy is linked to it.  This is because
the heart of the radiative transfer code is a matrix multiplication operation
conducted through numpy.dot, which in turn calls a BLAS library if it can find
one.  If it can't find one, your code will be many times slower.

On Linux, a good choice is OpenBLAS. You can install it on Ubuntu with::
  
  sudo apt install libopenblas-dev

On OS X, a good choice is Accelerate/vecLib, which should already be installed
by default.

To check if your numpy is linked to BLAS, do::

  numpy.__config__.show()

If blas_opt_info mentions OpenBLAS or vecLib, that's a good sign.  If it says
"NOT AVAILABLE", that's a bad sign.

Once you have a BLAS installed and linked to numpy, download PLATON,
install the requirements, and install PLATON itself::

  git clone https://github.com/ideasrule/platon.git
  cd platon/
  pip install -r requirements.txt
  python setup.py install

That's it!  To run unit tests to make sure everything runs::
  
  python setup.py test

The unit tests should also give you a good idea of how fast the code will be.
On a decent Ubuntu machine with OpenBLAS, it takes 2 minutes.
