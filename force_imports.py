# force_imports.py
import sys
import os

# CRITICAL: Force import order
import numpy
import numpy.core._multiarray_umath
import numpy.core._multiarray_tests
import numpy.random._common
import numpy.random.bit_generator
import numpy.random._bounded_integers
import numpy.random._mt19937
import numpy.random.mtrand
import numpy.random._philox
import numpy.random._pcg64
import numpy.random._sfc64
import numpy.random._generator

# Force import onnxruntime components in correct order
import onnxruntime.capi._pybind_state
import onnxruntime.capi.onnxruntime_pybind11_state
import onnxruntime