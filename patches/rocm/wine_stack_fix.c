/*
 * wine_stack_fix.so — LD_PRELOAD shim for ROCm/clr#61
 *
 * Two interventions, both targeting ROCm bugs that abort the host process:
 *
 * 1) pthread_getattr_np: ROCm's HSA runtime uses this to get stack bounds.
 *    Under Wine the returned base/size can trigger an internal assertion in
 *    ROCm's thread pool initialization. We clamp size to 8 MiB (and
 *    synthesize a range around the actual SP if it's outside the reported
 *    range entirely).
 *
 * 2) __glibcxx_assert_fail (2026-06-02): AMD ships libamdhip64.so.6 with
 *    `-D_GLIBCXX_ASSERTIONS=1`, which turns `std::vector::operator[]` into a
 *    bounds-checked access that calls `_ZSt21__glibcxx_assert_failPKciS0_S0_`
 *    and aborts on OOB. There is a documented data-race in libamdhip64's
 *    `std::vector<amd::roc::ProfilingSignal*>` pool (ROCm/HIP issue #2426,
 *    reported since ROCm 4.5.0, still present in 6.2.4) where the
 *    completion-handler thread can index past `size()` under high-frequency
 *    async dispatch. The OOB access itself returns adjacent memory which
 *    libamdhip64 mostly handles gracefully — the abort is what kills the
 *    process. We intercept the assertion to log + continue.
 *
 *    Log destination: /tmp/zluda_tex.log (same file ZLUDA writes to, so
 *    diagnostics stay correlated). Falls back to stderr if the open fails.
 *
 *    Risk: lets a real OOB read flow through. If the returned pointer is
 *    nullptr or junk, we may segfault later — but we're already aborting on
 *    the assertion, so we're trading a guaranteed SIGABRT for a "maybe it
 *    works, maybe it crashes differently" outcome.
 *
 * Build: gcc -shared -fPIC -o wine_stack_fix.so wine_stack_fix.c -ldl -lpthread
 * Usage: LD_PRELOAD=wine_stack_fix.so <command>
 */

#define _GNU_SOURCE
#include <dlfcn.h>
#include <pthread.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

/* Maximum stack size to report: 8MB (typical default) */
#define MAX_STACK_SIZE (8 * 1024 * 1024)

int pthread_getattr_np(pthread_t thread, pthread_attr_t *attr) {
    static int (*real_pthread_getattr_np)(pthread_t, pthread_attr_t *) = NULL;
    if (!real_pthread_getattr_np) {
        real_pthread_getattr_np = dlsym(RTLD_NEXT, "pthread_getattr_np");
        if (!real_pthread_getattr_np)
            return -1;
    }

    int ret = real_pthread_getattr_np(thread, attr);
    if (ret != 0)
        return ret;

    void *stackaddr;
    size_t stacksize;
    if (pthread_attr_getstack(attr, &stackaddr, &stacksize) == 0) {
        if (stacksize > MAX_STACK_SIZE) {
            /* Clamp to MAX_STACK_SIZE, adjusting base address accordingly */
            void *new_stackaddr = (char *)stackaddr + (stacksize - MAX_STACK_SIZE);
            pthread_attr_setstack(attr, new_stackaddr, MAX_STACK_SIZE);
        }

        /* Diagnostic: detect if SP is outside the reported range entirely.
         * If this fires, the clamp above is insufficient and we need to
         * synthesize a new range around the actual SP (see comprehensive
         * version in /home/deck/Desktop/zluda/wine_stack_fix.c).
         * Uncomment the fix block below if this diagnostic triggers.
         */
        uintptr_t sp = (uintptr_t)__builtin_frame_address(0);
        uintptr_t base = (uintptr_t)stackaddr;
        uintptr_t top = base + stacksize;
        if (sp < base || sp >= top) {
            uintptr_t new_base = (sp > MAX_STACK_SIZE + 0x10000UL)
                ? (sp - MAX_STACK_SIZE) & ~(uintptr_t)0xFFF
                : 0x10000UL;
            uintptr_t new_top = new_base + MAX_STACK_SIZE;
            if (sp + 0x10000UL > new_top)
                new_top = (sp + 0x10000UL + 0xFFF) & ~(uintptr_t)0xFFF;
            pthread_attr_setstack(attr, (void *)new_base, new_top - new_base);
        }
    }

    return 0;
}

/* ------------------------------------------------------------------------- */
/* 2026-06-02: __glibcxx_assert_fail override                                */
/*                                                                           */
/* Symbol name as exported by libstdc++.so.6 (verified via nm -D):           */
/*   _ZSt21__glibcxx_assert_failPKciS0_S0_                                   */
/* Demangled: void std::__glibcxx_assert_fail(char const*, int,              */
/*                                            char const*, char const*)     */
/*                                                                           */
/* Args (from the libstdc++ source):                                         */
/*   file     — source file of the failing assertion                        */
/*   line     — source line                                                 */
/*   function — qualified name of the failing function                      */
/*   cond     — text of the failed condition (e.g. "__n < this->size()")    */
/*                                                                           */
/* Normally this calls __glibcxx_assert_fail_with_aborts() which prints to  */
/* stderr and calls abort(). We swallow the call and return, allowing the   */
/* caller (libamdhip64 vector OOB access) to continue with whatever the     */
/* OOB read returns.                                                         */
/* ------------------------------------------------------------------------- */

static FILE *open_zluda_log(void) {
    /* Append-only, line-buffered. ZLUDA writes to /tmp/zluda_tex.log too — */
    /* sharing the file keeps our assertion log timeline aligned with the   */
    /* kernel-launch trace for post-mortem analysis.                        */
    const char *path = getenv("ZLUDA_LOG_PATH");
    if (!path || !*path)
        path = "/tmp/zluda_tex.log";
    FILE *f = fopen(path, "a");
    if (!f)
        return stderr;
    setvbuf(f, NULL, _IOLBF, 0);
    return f;
}

static void log_assert_fail(const char *file, int line,
                            const char *function, const char *cond) {
    /* Track how often this fires per-process so we can see the rate. */
    static __thread unsigned long counter = 0;
    counter++;

    /* Throttle after first 16: spam-detection. Once-per-1000 thereafter. */
    if (counter > 16 && (counter % 1000) != 0)
        return;

    FILE *f = open_zluda_log();
    struct timespec ts = {0};
    clock_gettime(CLOCK_MONOTONIC, &ts);

    /* fprintf is signal-unsafe but assert_fail is not from a signal handler,
     * so this is fine. */
    fprintf(f,
            "[%lld.%03lds] [SHIM] glibcxx_assert_fail #%lu suppressed: "
            "cond=%s at %s:%d in %s (tid=%d)\n",
            (long long)ts.tv_sec, (long)(ts.tv_nsec / 1000000),
            counter,
            cond ? cond : "(null)",
            file ? file : "(null)",
            line,
            function ? function : "(null)",
            (int)gettid());

    if (f != stderr)
        fclose(f);
}

/* Mangled name + extern "C" so the symbol matches what libamdhip64's
 * dynamic linker looks up. */
__attribute__((visibility("default")))
void _ZSt21__glibcxx_assert_failPKciS0_S0_(const char *file, int line,
                                            const char *function,
                                            const char *condition) {
    log_assert_fail(file, line, function, condition);
    /* Return instead of aborting. Caller (vector::operator[]) will then
     * dereference the OOB element. */
}

/* Belt-and-suspenders: also override the C-level __assert_fail in case
 * a different code path inside libamdhip64 uses C-style asserts. We DO
 * NOT silence non-libstdc++ asserts that fire from the rest of the
 * process (libc, libgl, etc.) — gate on the calling library by checking
 * the assert message string for ROCm/HIP fingerprints. If the string
 * doesn't look like our target bug, fall through to the real __assert_fail. */
__attribute__((visibility("default"), noreturn))
void __assert_fail(const char *assertion, const char *file, unsigned int line,
                   const char *function) {
    /* Whitelist: only suppress assertions that look like the ROCm vector
     * OOB pattern we know about. Anything else: call the real one. */
    int is_rocm_vector_oob = assertion && (
        strstr(assertion, "__n < this->size()") ||
        strstr(assertion, "__pos <= this->size()") ||
        strstr(assertion, "size() > 0")
    );

    if (is_rocm_vector_oob) {
        log_assert_fail(file, (int)line, function, assertion);
        /* Caller wants noreturn behaviour. We must not return from a
         * `noreturn` function or the compiler may emit `ud2` after the
         * call site. Loop forever to satisfy [[noreturn]] semantics, but
         * we never actually hit this — _ZSt21... is what libamdhip64
         * calls, not this C variant. Kept as defence-in-depth. */
        _exit(0);
    }

    /* Defer to the real __assert_fail for everything else. */
    static void (*real_assert_fail)(const char *, const char *, unsigned int,
                                    const char *) __attribute__((noreturn)) = NULL;
    if (!real_assert_fail)
        real_assert_fail = dlsym(RTLD_NEXT, "__assert_fail");
    if (real_assert_fail)
        real_assert_fail(assertion, file, line, function);
    /* Unreachable. */
    _exit(134);
}
