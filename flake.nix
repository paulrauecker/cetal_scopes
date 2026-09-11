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
        isLinux = pkgs.stdenv.isLinux;
      in {
        devShells.default = pkgs.mkShell {
          packages = [ python pkgs.uv ];

          env = {
            # Don't let uv fetch its own Python; use the Nix-provided one
            UV_PYTHON_DOWNLOADS = "never";
            UV_PYTHON = python.interpreter;
          } // pkgs.lib.optionalAttrs isLinux {
            # Lets compiled extensions (numpy, pydantic-core, etc.) find libs
            LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath pkgs.pythonManylinuxPackages.manylinux1;
          };

          shellHook = if isLinux then ''
            export PYTHONPATH="${tkinterSitePackages}"
            [ -d .venv ] || uv sync
          '' else ''
            unset PYTHONPATH
            [ -d .venv ] || uv sync
          '';
        };
      });
}
