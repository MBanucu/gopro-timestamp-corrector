{
  description = "GoPro media timestamp correction tool";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        deps = with pkgs; [ exiftool e2fsprogs exfat libfaketime ];
        python = pkgs.python3.withPackages (ps: [ ps.tkinter ]);
        src = pkgs.lib.cleanSource ./.;
      in {
        devShells.default = pkgs.mkShell {
          packages = deps ++ [ python pkgs.bashInteractive ];
        };

        packages.cli = pkgs.stdenvNoCC.mkDerivation {
          name = "gopro-time-correction-cli";
          inherit src;
          dontBuild = true;
          installPhase = ''
            mkdir -p $out/bin $out/lib
            cp *.py $out/lib/
            python_lib="$out/lib"
            python_bin="${python}/bin/python3"
            cat > $out/bin/correct-gopro-timestamps << WRAPPER
#!${pkgs.bash}/bin/bash
export PATH="${pkgs.lib.makeBinPath deps}:\$PATH"
export PYTHONPATH="$python_lib:\$PYTHONPATH"
exec $python_bin "$python_lib/correct_timestamps.py" "\$@"
WRAPPER
            chmod +x $out/bin/correct-gopro-timestamps
          '';
        };

        packages.gui = pkgs.stdenvNoCC.mkDerivation {
          name = "gopro-time-correction-gui";
          inherit src;
          dontBuild = true;
          installPhase = ''
            mkdir -p $out/bin $out/lib
            cp *.py $out/lib/
            python_lib="$out/lib"
            python_bin="${python}/bin/python3"
            cat > $out/bin/gopro-timestamp-gui << WRAPPER
#!${pkgs.bash}/bin/bash
export PATH="${pkgs.lib.makeBinPath deps}:\$PATH"
export PYTHONPATH="$python_lib:\$PYTHONPATH"
exec $python_bin "$python_lib/gui.py" "\$@"
WRAPPER
            chmod +x $out/bin/gopro-timestamp-gui
          '';
        };

        packages.test = pkgs.stdenvNoCC.mkDerivation {
          name = "gopro-time-correction-test";
          inherit src;
          dontBuild = true;
          installPhase = ''
            python_test="${pkgs.python3.withPackages (ps: [ ps.tkinter ps.coverage ])}/bin/python3"
            mkdir -p $out/bin
            cat > $out/bin/run-tests << WRAPPER
#!${pkgs.bash}/bin/bash
exec $python_test -m coverage run --source tzcombobox -m unittest discover -v test
WRAPPER
            cat > $out/bin/coverage-report << WRAPPER
#!${pkgs.bash}/bin/bash
exec $python_test -m coverage report -m --include="*tzcombobox*"
WRAPPER
            chmod +x $out/bin/run-tests $out/bin/coverage-report
          '';
        };

        packages.default = self.packages.${system}.cli;

        apps.default = {
          type = "app";
          program = "${self.packages.${system}.cli}/bin/correct-gopro-timestamps";
        };

        apps.gui = {
          type = "app";
          program = "${self.packages.${system}.gui}/bin/gopro-timestamp-gui";
        };

        apps.test = {
          type = "app";
          program = "${self.packages.${system}.test}/bin/run-tests";
        };
      });
}
