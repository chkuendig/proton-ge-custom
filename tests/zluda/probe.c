#include <windows.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv)
{
    HMODULE module = LoadLibraryA("nvcuda.dll");
    int on = argc == 2 && !strcmp(argv[1], "on");
    if (!module) { fprintf(stderr, "LoadLibrary failed: %lu\n", GetLastError()); return 1; }
    int (WINAPI *init)(unsigned) = (void *)GetProcAddress(module, "cuInit");
    if (!on) {
        if (init) return 2;
    } else {
        if (!init || init(0) != 0 || init(1) != 1) return 3;
        if (GetProcAddress(module, "DllMain") || GetProcAddress(module, "zluda_wine_call")) return 4;
    }
    printf("PASS: PE%u %s CUDA module\n", (unsigned)(8 * sizeof(void *)), on ? "on" : "off");
    return 0;
}
