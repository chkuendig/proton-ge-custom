/*
 * wine_stack_fix.so — LD_PRELOAD shim for ROCm/clr#61
 *
 * ROCm's HSA runtime calls pthread_getattr_np() to get stack bounds,
 * but under Wine the returned stack base/size can trigger an assertion
 * failure in ROCm's thread pool initialization.
 *
 * This shim intercepts pthread_getattr_np() and clamps the reported
 * stack size to a reasonable value, preventing the assertion.
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
            fprintf(stderr, "[wine_stack_fix] WARNING: SP %p outside stack [%p, %p) — clamp insufficient\n",
                    (void *)sp, stackaddr, (void *)top);
            /* DISABLED: synthesize range around actual SP
            uintptr_t new_base = (sp > MAX_STACK_SIZE + 0x10000UL)
                ? (sp - MAX_STACK_SIZE) & ~(uintptr_t)0xFFF
                : 0x10000UL;
            uintptr_t new_top = new_base + MAX_STACK_SIZE;
            if (sp + 0x10000UL > new_top)
                new_top = (sp + 0x10000UL + 0xFFF) & ~(uintptr_t)0xFFF;
            pthread_attr_setstack(attr, (void *)new_base, new_top - new_base);
            */
        }
    }

    return 0;
}
