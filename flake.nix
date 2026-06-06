{
  description = "GoPro media timestamp correction tool";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    exfat-raw = {
      url = "github:MBanucu/exfat-raw/main";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.flake-utils.follows = "flake-utils";
    };
    pyexiftool-nix = {
      url = "github:MBanucu/pyexiftool-nix/main";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.flake-utils.follows = "flake-utils";
    };
  };

  outputs = { self, nixpkgs, flake-utils, exfat-raw, pyexiftool-nix }:
    let
      eachSystem = flake-utils.lib.eachDefaultSystem;
      nixosSystem = "x86_64-linux";
      nixosPkgs = nixpkgs.legacyPackages.${nixosSystem};

      # Bundle the source tree for NixOS test VM access
      nixosTestSrc = nixosPkgs.stdenvNoCC.mkDerivation {
        name = "gopro-nixos-test-src";
        src = nixosPkgs.lib.cleanSource ./.;
        phases = [ "installPhase" ];
        installPhase = ''
          mkdir -p $out
          cp -r $src/* $out/
          chmod -R +w $out
        '';
      };

      nixosTestDeps = with nixosPkgs; [ exiftool e2fsprogs exfat libfaketime xvfb sudo ];
      nixosTestPython = nixosPkgs.python3.withPackages (ps: [ ps.tkinter pyexiftool-nix.packages.${nixosSystem}.pyexiftool ]);
      nixosTestErSp = exfat-raw.lib.sitePackages nixosSystem;

      nixosTest = nixosPkgs.testers.nixosTest {
        name = "gopro-timestamp-corrector";
        nodes.machine = { pkgs, lib, ... }: {
          virtualisation.cores = 4;
          virtualisation.memorySize = 4096;
          # virtualisation.diskSize = 32768;  # default is sufficient; larger disk introduced regressions
          time.timeZone = "Europe/Berlin";

          boot.kernelParams = [ "loglevel=3" ];
          services.journald.extraConfig = ''
            ForwardToConsole=no
          '';

          environment.systemPackages = with pkgs; [
            exiftool e2fsprogs exfat libfaketime xvfb sudo
            bashInteractive coreutils gnutar gzip
          ];

          boot.kernelModules = [ "exfat" ];
          boot.supportedFilesystems = [ "exfat" ];

          users.users.test = {
            isNormalUser = true;
            extraGroups = [ "wheel" ];
          };

          security.sudo.enable = true;
          security.sudo.wheelNeedsPassword = false;
          security.sudo.extraRules = [{
            groups = [ "wheel" ];
            commands = [
              { command = "ALL"; options = [ "NOPASSWD" ]; }
            ];
          }];
        };

        testScript = ''
          machine.start()
          machine.wait_for_unit("multi-user.target")

          src_path = "${nixosTestSrc}"
          machine.succeed(f"cp -a {src_path}/. /tmp/gopro-test")
          machine.succeed("chown -R test:users /tmp/gopro-test")

          bin_path = "${nixosPkgs.lib.makeBinPath nixosTestDeps}"
          python_bin = "${nixosTestPython}/bin/python3"

          machine.succeed("echo '--- NixOS test: VM kernel ---'")
          kernel = machine.succeed("uname -a")
          machine.succeed(f"echo 'VM kernel: {kernel.strip()}'")
          machine.succeed("echo '--- NixOS test: copying source ---'")
          machine.succeed("echo '--- NixOS test: environment check ---'")
          er_sp = "${nixosTestErSp}"

          machine.succeed(
              "cd /tmp/gopro-test && "
              + f"export PATH={bin_path}:$PATH && "
              + f"export PYTHONPATH=/tmp/gopro-test/src:/tmp/gopro-test/test:{er_sp} && "
              + f"DISPLAY=:99 {python_bin} -m env_check /tmp/gopro-test/test 2>&1"
          )
          machine.succeed("echo '--- NixOS test: starting Xvfb ---'")
          machine.succeed("Xvfb :99 -screen 0 1024x768x24 &>/dev/null & sleep 1")

          machine.succeed(
              "cd /tmp/gopro-test && "
              + f"export PATH={bin_path}:$PATH && "
              + f"export PYTHONPATH=/tmp/gopro-test/src:/tmp/gopro-test/test:{er_sp} && "
              + f"DISPLAY=:99 {python_bin} -m test.run_parallel -v -j 1 2>&1"
          )
        '';
      };
    in (eachSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        deps = with pkgs; [ exiftool e2fsprogs exfat libfaketime xvfb ];
        pyexiftool = pyexiftool-nix.packages.${system}.pyexiftool;
        python = pkgs.python3.withPackages (ps: [ ps.tkinter pyexiftool ]);
        test-python = pkgs.python3.withPackages (ps: [ ps.tkinter ps.coverage pyexiftool ]);
        src = pkgs.lib.cleanSource ./.;
        er_sp = exfat-raw.lib.sitePackages system;
      in {
        devShells.default = pkgs.mkShell {
          packages = deps ++ [ python exfat-raw.packages.${system}.default pkgs.bashInteractive ];
          shellHook = ''
            export PYTHONPATH="${er_sp}:$PYTHONPATH"
            export PYTHONPATH="${src}/src:$PYTHONPATH:${src}/test"
          '';
        };

        packages.cli = pkgs.stdenvNoCC.mkDerivation {
          name = "gopro-time-correction-cli";
          inherit src;
          dontBuild = true;
          installPhase = ''
            mkdir -p $out/bin $out/lib/gui/steps
            cp src/*.py $out/lib/
            cp src/gui/*.py $out/lib/gui/
            cp src/gui/steps/*.py $out/lib/gui/steps/
            python_lib="$out/lib"
            python_bin="${python}/bin/python3"
            cat > $out/bin/correct-gopro-timestamps << WRAPPER
        #!${pkgs.bash}/bin/bash
        export PATH="${pkgs.lib.makeBinPath deps}:\$PATH"
        export PYTHONPATH="$python_lib:${er_sp}:\$PYTHONPATH"
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
            mkdir -p $out/bin $out/lib/gui/steps
            cp src/*.py $out/lib/
            cp src/gui/*.py $out/lib/gui/
            cp src/gui/steps/*.py $out/lib/gui/steps/
            python_lib="$out/lib"
            python_bin="${python}/bin/python3"
            cat > $out/bin/gopro-timestamp-gui << WRAPPER
        #!${pkgs.bash}/bin/bash
        export PATH="${pkgs.lib.makeBinPath deps}:\$PATH"
        export PYTHONPATH="$python_lib:${er_sp}:\$PYTHONPATH"
        exec $python_bin "$python_lib/gui/app.py" "\$@"
        WRAPPER
            chmod +x $out/bin/gopro-timestamp-gui
          '';
        };

        packages.test = pkgs.stdenvNoCC.mkDerivation {
          name = "gopro-time-correction-test";
          inherit src;
          dontBuild = true;
          installPhase = ''
            python_test="${test-python}/bin/python3"
            mkdir -p $out/bin
            deps_bin="${pkgs.lib.makeBinPath deps}"
            cat > $out/bin/run-tests << WRAPPER
        #!${pkgs.bash}/bin/bash
        export PATH="$deps_bin:\$PATH"
        export PYTHONPATH="\$PYTHONPATH:$src:$src/src:$out/lib:${er_sp}"
        Xvfb :99 -screen 0 1024x768x24 &>/dev/null &
        XVFB_PID=\$!
        sleep 1
        DISPLAY=:99 $python_test -m test.run_parallel --coverage "\$@"
        EXIT_CODE=\$?
        kill \$XVFB_PID 2>/dev/null
        exit \$EXIT_CODE
        WRAPPER
            cat > $out/bin/coverage-report << WRAPPER
        #!${pkgs.bash}/bin/bash
        exec $python_test -m coverage report -m --include="$src/src/*"
        WRAPPER
            chmod +x $out/bin/run-tests $out/bin/coverage-report
          '';
        };

        packages.server = pkgs.stdenvNoCC.mkDerivation {
          name = "gopro-time-correction-server";
          inherit src;
          dontBuild = true;
          installPhase = ''
            mkdir -p $out/bin $out/lib
            cp src/*.py $out/lib/
            python_lib="$out/lib"
            python_bin="${python}/bin/python3"
            cat > $out/bin/gopro-exiftool-server << WRAPPER
        #!${pkgs.bash}/bin/bash
        export PATH="${pkgs.lib.makeBinPath deps}:\$PATH"
        export PYTHONPATH="$python_lib:${er_sp}:\$PYTHONPATH"
        exec $python_bin "$python_lib/exiftool_server.py" "\$@"
        WRAPPER
            chmod +x $out/bin/gopro-exiftool-server
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

        apps.server = {
          type = "app";
          program = "${self.packages.${system}.server}/bin/gopro-exiftool-server";
        };
      })) // {
        checks.${nixosSystem}.nixos-test = nixosTest;
      };
}
