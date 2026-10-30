/* Loader-precedence tripwire: loading this native DLL must fail the CUDA probe. */
#include <windows.h>
__declspec(dllexport) int WINAPI cuInit(unsigned int flags) { (void)flags; return 999; }
BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved)
{ (void)instance; (void)reason; (void)reserved; return TRUE; }
