import os
import urllib.request
import tarfile
import numpy as np
import shutil

# URL for the standard SIFT1M dataset
URL = "ftp://ftp.irisa.fr/local/texmex/corpus/sift.tar.gz"
FILENAME = "sift.tar.gz"
DATA_DIR = "sift_data"

def download_and_extract():
    if not os.path.exists(FILENAME):
        print(f"--- Downloading SIFT1M from {URL} ---")
        urllib.request.urlretrieve(URL, FILENAME)
    
    if not os.path.exists(DATA_DIR):
        print("--- Extracting... ---")
        with tarfile.open(FILENAME, "r:gz") as tar:
            tar.extractall()
        # Rename/Move for cleanliness
        if os.path.exists("sift"):
            shutil.move("sift", DATA_DIR)
    print("--- Ready. Data is in 'sift_data/' ---")

def read_fvecs(filename):
    """
    Reads the standard .fvecs format used by SIFT/GIST benchmarks.
    Format: [int32 (dim), float32 (v1), float32 (v2), ...]
    """
    print(f"Loading {filename}...")
    # Read as int32 to get dimensions
    fv = np.fromfile(filename, dtype=np.int32)
    if fv.size == 0:
        return np.zeros((0, 0))
    
    dim = fv[0]
    # Reshape: The file is a flat array of (dim + 1) chunks
    # We view it as float32, then slice off the dimension header column
    return fv.reshape(-1, dim + 1)[:, 1:].view(np.float32)

if __name__ == "__main__":
    download_and_extract()
    
    # Verification
    base = read_fvecs(f"{DATA_DIR}/sift_base.fvecs")
    print(f"Base Dataset Shape: {base.shape} (Should be 1,000,000 x 128)")
    
    query = read_fvecs(f"{DATA_DIR}/sift_query.fvecs")
    print(f"Query Dataset Shape: {query.shape} (Should be 10,000 x 128)")
    
    # Save as standard .npy for faster loading in experiment
    np.save(f"{DATA_DIR}/dataset.npy", base)
    np.save(f"{DATA_DIR}/queries.npy", query)
    print("--- Converted to .npy for speed ---")