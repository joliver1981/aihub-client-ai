# copy_onnxruntime.py
import shutil
import os
import onnxruntime
from pathlib import Path

# Get ONNX runtime installation path
onnx_path = Path(onnxruntime.__file__).parent
print(f"Copying ONNX Runtime from: {onnx_path}")

# Create destination
dest_path = Path("onnxruntime_files")
if dest_path.exists():
    shutil.rmtree(dest_path)

# Copy entire onnxruntime folder
shutil.copytree(onnx_path, dest_path / "onnxruntime")
print(f"ONNX Runtime copied to: {dest_path}")

# Also copy numpy's .libs folder if it exists (ONNX might need it)
import numpy
numpy_libs = Path(numpy.__file__).parent / ".libs"
if numpy_libs.exists():
    shutil.copytree(numpy_libs, dest_path / "numpy.libs")
    print("Also copied numpy .libs")