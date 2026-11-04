/* SteamOS build link compatibility: forward to the real old glibc ABI.
 * No numerical implementations or guesses about valid arguments here. */
extern double zluda_glibc_cosh(double);
extern double zluda_glibc_sinh(double);
__asm__(".symver zluda_glibc_cosh,cosh@GLIBC_2.2.5");
__asm__(".symver zluda_glibc_sinh,sinh@GLIBC_2.2.5");
__attribute__((visibility("hidden"))) double cosh(double x) {
    return zluda_glibc_cosh(x);
}
__attribute__((visibility("hidden"))) double sinh(double x) {
    return zluda_glibc_sinh(x);
}
#ifdef ZLUDA_TEST_MATHCOMPAT
double zluda_test_cosh(double x) { return cosh(x); }
double zluda_test_sinh(double x) { return sinh(x); }
#endif
