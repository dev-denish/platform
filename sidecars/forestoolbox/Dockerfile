# VNV Pipeline Phase 2 - NDFI sidecar (R / plumber).
# STANDALONE image on purpose: not referenced from docker-compose.yml, arq, or
# the FastAPI app. Build + run it by hand:
#   docker build -t denish1/dmrv-forestoolbox sidecars/forestoolbox/
#   docker run --rm -p 8002:8002 -v /some/host/dir:/data denish1/dmrv-forestoolbox
#
# Base: rocker/r-ver:4.4.1 = Ubuntu 22.04 (jammy) + R 4.4.1. Its default CRAN
# repo is the Posit Package Manager jammy *binary* mirror, pinned to the
# 2024-10-30 snapshot (verified in-image with getOption("repos")), so
# install.packages()/install2.r download prebuilt binaries instead of compiling
# - which is why this builds in minutes and not an hour. rocker/geospatial
# would also work and pre-bundles GDAL/GEOS/PROJ + raster, but it drags in
# RStudio Server + tidyverse + sf/terra/stars we do not use, for ~3x the image.
FROM rocker/r-ver:4.4.1

# raster's backend (terra) links GDAL/GEOS/PROJ, so the C libs must exist even
# when installing R binaries. jammy names: libgdal-dev (GDAL 3.4.1),
# libgeos-dev, libproj-dev. git is needed because ForesToolboxRS is GitHub-only.
RUN apt-get update && apt-get install -y --no-install-recommends \
      git \
      libgdal-dev \
      libgeos-dev \
      libproj-dev \
    && rm -rf /var/lib/apt/lists/*

# ForesToolboxRS 0.2.0 Imports, minus the packages that ship with R itself
# (methods, stats, graphics, grDevices), plus plumber (the HTTP server this
# sidecar runs on) and remotes (to install ForesToolboxRS from GitHub).
RUN install2.r --error --skipinstalled --ncpus -1 \
      remotes \
      plumber \
      forecast \
      dplyr \
      raster \
      zoo \
      factoextra \
      caret \
      e1071 \
      randomForest \
    && rm -rf /tmp/downloaded_packages

RUN Rscript -e 'remotes::install_github("ytarazona/ForesToolboxRS", upgrade = "never")'

WORKDIR /app
COPY plumber.R test_ndfi.R ./

# Self-check runs at build time: if sma() or the NDFI formula is broken, the
# build fails here rather than in production.
RUN Rscript test_ndfi.R

# 8002, not 8000/8001: the FastAPI backend containers in this repo already use
# 8000 (compose baseline) and 8001 (deploy/), so this keeps the sidecar clear
# of them if it is ever put on the same host/network.
EXPOSE 8002
CMD ["Rscript", "-e", "plumber::plumb('/app/plumber.R')$run(host = '0.0.0.0', port = 8002)"]
