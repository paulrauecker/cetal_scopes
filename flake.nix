{
  description = "Python + uv dev shell";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils, ... }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        python = pkgs.python312;
        # Nix's python312 does not ship _tkinter, so matplotlib falls back to
        # the non-interactive Agg backend. Expose Tk on PYTHONPATH so the uv
        # venv can use the TkAgg backend for GUI apps such as the viewer.
        tkinterSitePackages =
          "${pkgs.python312Packages.tkinter}/${python.sitePackages}";
        # The Spectrum vendor driver (libspcm_linux.so, installed system-wide by
        # the vendor package at /usr/lib/x86_64-linux-gnu) carries no SONAME and
        # spcm_core loads it by bare name with no path override, so it has to be
        # findable via the linker search path. Putting the whole system lib dir
        # on LD_LIBRARY_PATH is NOT safe: it also holds libpython3.12.so, which
        # shadows Nix's libpython and breaks ctypes itself with an "undefined
        # symbol: _PyErr_SetLocaleString" import error (confirmed on this host).
        # So build a narrow directory containing only a symlink to the one
        # vendor library we need, and search that instead.
        vendorDriverLibDir = pkgs.runCommand "spcm-vendor-lib" { } ''
          mkdir -p "$out"
          ln -s /usr/lib/x86_64-linux-gnu/libspcm_linux.so "$out/libspcm_linux.so"
        '';
        isLinux = pkgs.stdenv.isLinux;
      in {
        devShells.default = pkgs.mkShell {
          # nodejs: `pyright` (via pyright-python) downloads its own generic
          # prebuilt Node.js binary on first run and prefers a `node` already
          # on PATH over that download. On this host the prebuilt binary
          # segfaults immediately (confirmed: glibc/CPU baseline mismatch),
          # while Nix's own nodejs works, so put it on PATH to make `uv run
          # pyright` usable at all.
          #
          # git: the pattern above turns out not to be specific to Python
          # wheels -- this host's system /usr/bin/git ALSO segfaults
          # (confirmed), and it fails *silently* (`git status`/`git diff`
          # print nothing and exit 139, which is easy to misread as "no
          # changes"). Put Nix's git on PATH too so it shadows the broken one.
          packages = [ python pkgs.uv pkgs.nodejs pkgs.git ];

          env = {
            # Don't let uv fetch its own Python; use the Nix-provided one
            UV_PYTHON_DOWNLOADS = "never";
            UV_PYTHON = python.interpreter;
          } // pkgs.lib.optionalAttrs isLinux {
            # Lets compiled extensions (numpy, pydantic-core, etc.) find libs
            LD_LIBRARY_PATH =
              pkgs.lib.makeLibraryPath pkgs.pythonManylinuxPackages.manylinux1
              + ":${vendorDriverLibDir}";
          };

          shellHook = if isLinux then ''
            export PYTHONPATH="${tkinterSitePackages}"
            [ -d .venv ] || uv sync
            # ruff's PyPI wheel bundles a prebuilt native binary that
            # segfaults immediately on this host (same root cause as the
            # Node.js binary above: it's not built by Nix for this machine).
            # Swap in Nix's own ruff build so `uv run ruff` actually runs
            # instead of silently crashing with no output.
            [ -x .venv/bin/ruff ] && ln -sf ${pkgs.ruff}/bin/ruff .venv/bin/ruff
          '' else ''
            unset PYTHONPATH
            [ -d .venv ] || uv sync
          '';
        };
      });
}
