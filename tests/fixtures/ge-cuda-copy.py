if use_nvcuda and use_nvapi:
    try_copy(g_proton.arch_pe_dir("wine/nvidia-libs/nvcuda", False) + "nvcuda.dll.so", "drive_c/windows/system32/nvcuda.dll",
            prefix=self.prefix_dir, track_file=tracked_files, link_debug=True)
    g_session.dlloverrides["nvcuda"] = "n,b"
else:
    nvcuda64_dll = self.prefix_dir + "drive_c/windows/system32/nvcuda.dll"
    if file_exists(nvcuda64_dll, follow_symlinks=False):
        os.unlink(nvcuda64_dll)
    try_copy(g_proton.default_pfx_dir + "drive_c/windows/system32/nvcuda.dll", "drive_c/windows/system32",
            prefix=self.prefix_dir, track_file=tracked_files, link_debug=True)

if use_nvcuda and use_nvapi and not disable_nvlibs32:
    try_copy(g_proton.arch_pe_dir("wine/nvidia-libs/nvcuda", True) + "nvcuda.dll.so", "drive_c/windows/syswow64/nvcuda.dll",
             prefix=self.prefix_dir, track_file=tracked_files, link_debug=True)
    g_session.dlloverrides["nvcuda"] = "n,b"
else:
    nvcuda32_dll = self.prefix_dir + "drive_c/windows/syswow64/nvcuda.dll"
    if file_exists(nvcuda32_dll, follow_symlinks=False):
        os.unlink(nvcuda32_dll)
    try_copy(g_proton.default_pfx_dir + "drive_c/windows/syswow64/nvcuda.dll", "drive_c/windows/syswow64",
             prefix=self.prefix_dir, track_file=tracked_files, link_debug=True)

