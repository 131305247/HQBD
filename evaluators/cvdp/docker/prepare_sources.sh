#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCES_DIR="$ROOT_DIR/docker/_sources"
PCIE_DIR="$ROOT_DIR/docker/_sources_cocotbext_pcie"

mkdir -p "$SOURCES_DIR"

clone_or_update() {
  local url="$1"
  local ref="$2"
  local dest="$3"

  if [ -d "$dest/.git" ]; then
    echo "Updating $dest"
    git -C "$dest" fetch --depth 1 origin "$ref"
    git -C "$dest" reset --hard
    git -C "$dest" clean -fdx
    git -C "$dest" checkout -f FETCH_HEAD
  elif [ -d "$dest" ] && [ "$(ls -A "$dest" 2>/dev/null)" ]; then
    echo "ERROR: $dest exists but is not a git checkout; move it away and rerun." >&2
    exit 1
  else
    echo "Cloning $url ($ref) -> $dest"
    rm -rf "$dest"
    git clone --depth 1 --branch "$ref" "$url" "$dest"
  fi
}

clone_or_update https://github.com/steveicarus/iverilog.git v13_0 "$SOURCES_DIR/iverilog"
clone_or_update https://github.com/YosysHQ/yosys.git yosys-0.40 "$SOURCES_DIR/yosys"
# The yosys-0.40 checkout does not contain abc as a git submodule here, but
# Dockerfile.sim.local builds with ABCREV=default to avoid fetching during docker
# build. Pre-clone YosysHQ/abc into yosys/abc for that path.
clone_or_update https://github.com/YosysHQ/abc.git yosys-experimental "$SOURCES_DIR/yosys/abc"
clone_or_update https://github.com/verilator/verilator.git v5.038 "$SOURCES_DIR/verilator"

if [ -d "$PCIE_DIR/.git" ]; then
  echo "Updating $PCIE_DIR"
  git -C "$PCIE_DIR" fetch --depth 1 origin 92732edd2d8cef002f0e984697ff31ccfe8a19a9
  git -C "$PCIE_DIR" reset --hard
  git -C "$PCIE_DIR" clean -fdx
  git -C "$PCIE_DIR" checkout -f FETCH_HEAD
elif [ -d "$PCIE_DIR" ] && [ "$(ls -A "$PCIE_DIR" 2>/dev/null)" ]; then
  echo "ERROR: $PCIE_DIR exists but is not a git checkout; move it away and rerun." >&2
  exit 1
else
  echo "Cloning cocotbext-pcie -> $PCIE_DIR"
  rm -rf "$PCIE_DIR"
  git clone https://github.com/alexforencich/cocotbext-pcie.git "$PCIE_DIR"
  git -C "$PCIE_DIR" checkout 92732edd2d8cef002f0e984697ff31ccfe8a19a9
fi

echo "Docker source cache is ready."
