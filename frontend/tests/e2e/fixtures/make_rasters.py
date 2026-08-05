import numpy as np
import rasterio
from rasterio.transform import from_origin

def write_classified(path, values, size=64):
    h = w = size
    arr = np.zeros((h, w), dtype="uint8")
    n = len(values)
    band_h = h // n
    for i, v in enumerate(values):
        lo = i * band_h
        hi = h if i == n - 1 else (i + 1) * band_h
        arr[lo:hi, :] = v
    profile = dict(
        driver="GTiff", height=h, width=w, count=1, dtype="uint8",
        crs="EPSG:32643", transform=from_origin(640000, 1445000, 10, 10), nodata=0,
    )
    with rasterio.open(path, "w", **profile) as d:
        d.write(arr, 1)

def write_raw(path, size=64):
    h = w = size
    rng = np.random.default_rng(42)
    arr = (rng.random((h, w)) * 200 + 20).astype("uint8")
    profile = dict(
        driver="GTiff", height=h, width=w, count=1, dtype="uint8",
        crs="EPSG:32643", transform=from_origin(640000, 1445000, 10, 10), nodata=0,
    )
    with rasterio.open(path, "w", **profile) as d:
        d.write(arr, 1)

write_classified("layer_a_3class.tif", [1, 2, 3])
write_classified("layer_b_9class.tif", list(range(1, 10)))
write_raw("layer_c_raw.tif")
print("OK")
