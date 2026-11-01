/* SPDX-License-Identifier: MIT
 * Test-only native CUDA provider. PCI location comes from the Wine/DXVK adapter probe.
 * No HIP, GPU device, production backend, or bridge implementation is imported.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static void audit(const char *event) {
    FILE *f=fopen(getenv("NVAPI_PROVIDER_AUDIT"),"a");
    if (!f) abort();
    fprintf(f,"%s\n",event); fclose(f);
}
__attribute__((constructor)) static void loaded(void) {audit("backend-loaded");}
int cuInit(unsigned flags) {audit("cuInit");return flags?1:0;}
int cuDriverGetVersion(int *version) {if(!version)return 1;*version=12020;return 0;}
int cuDeviceGetCount(int *count) {audit("cuDeviceGetCount");if(!count)return 1;*count=1;return 0;}
int cuDeviceGet(int *device,int ordinal) {audit("cuDeviceGet");if(!device||ordinal)return 101;*device=0;return 0;}
int cuDeviceGetAttribute(int *value,int attribute,int device) {
    audit("cuDeviceGetAttribute");if(!value||device)return 101;
    int index=attribute==50?0:attribute==33?1:attribute==34?2:-1;
    if(index<0)return 1;
    FILE *f=fopen(getenv("NVAPI_PROVIDER_PCI"),"rb");if(!f)return 1;
    unsigned values[3];size_t n=fread(values,sizeof(values),1,f);fclose(f);
    if(n!=1)return 1;
    *value=(int)values[index];
    return 0;
}
