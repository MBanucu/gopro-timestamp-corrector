# Sparse exFAT Image Report

## Overview
This report documents the procedure for creating a sparse, writable exFAT disk image. This method captures only used blocks while preserving metadata (like birth times).

## Workflow

1. **Creation:**
    ```bash
    # Unmount the partition first
    udisksctl unmount -b /dev/sda1
    
    # Clone partition using nix-shell to provide partclone binaries
    nix-shell -p partclone --run "
      sudo partclone.exfat -c -s /dev/sda1 -o sda1_raw.img &&
      sudo partclone.restore -s sda1_raw.img -o sda1_sparse.img -W &&
      rm sda1_raw.img
    "

    # Set permissions so the user can mount the image without sudo
    sudo chown $USER:users sda1_sparse.img
    chmod 644 sda1_sparse.img
    ```

2. **Mounting:**
    ```bash
    # Setup loop device (Nautilus will automatically mount it)
    udisksctl loop-setup -f sda1_sparse.img
    ```

## Results
*   **Metadata:** Birth and modification times are preserved exactly.
*   **Sparsity:** Consumes only ~8.5 MB of physical disk space for an 8.0 GB partition.
*   **Access:** Automatically mounted as read-write, owned by the user, and visible in Nautilus.

## Maintenance
When finished, detach the image:
```bash
udisksctl unmount -b /dev/loopX
```
