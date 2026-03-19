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
    }

    return 0;
}
