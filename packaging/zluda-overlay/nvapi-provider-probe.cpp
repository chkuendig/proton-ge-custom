// SPDX-License-Identifier: MIT
// Public CUDA/NVAPI/DXGI/Vulkan contracts only; test-only Wine adapter probe.
#define VK_NO_PROTOTYPES
#include <windows.h>
#include <dxgi1_4.h>
#include <vulkan/vulkan.h>
#define __NVAPI_EMPTY_SAL
#include <nvapi.h>
#undef __NVAPI_EMPTY_SAL
#include <nvapi_lite_salend.h>
#include <cstdio>
#include <cstring>
struct InteropFactory: IUnknown {
    virtual void STDMETHODCALLTYPE GetVulkanInstance(VkInstance*,PFN_vkGetInstanceProcAddr*)=0;
};
struct InteropAdapter: IUnknown {
    virtual void STDMETHODCALLTYPE GetVulkanHandles(VkInstance*,VkPhysicalDevice*)=0;
};
static const GUID factoryId={0x4c5e1b0d,0xb0c8,0x4131,{0xbf,0xd8,0x9b,0x24,0x76,0xf7,0xf4,0x08}};
static const GUID adapterId={0x3a6d8f2c,0xb0e8,0x4ab4,{0xb4,0xdc,0x4f,0xd2,0x48,0x91,0xbf,0xa5}};
#define REQUIRE(x) do { if(!(x)) {printf("FAIL line=%d expression=%s\n",__LINE__,#x); return 1;} } while(0)
int main(int argc,char **argv) {
    REQUIRE(argc==5); // PCI filename, NVAPI DLL, mode, PCI mismatch control
    IDXGIFactory1 *factory=nullptr;
    HRESULT hr=CreateDXGIFactory1(__uuidof(IDXGIFactory1),(void**)&factory);
    if(FAILED(hr)){printf("UNAVAILABLE DXGI factory hr=%08lx\n",hr);return 77;}
    IDXGIAdapter1 *adapter=nullptr;
    if(factory->EnumAdapters1(0,&adapter)!=S_OK){puts("UNAVAILABLE no DXVK adapter");return 77;}
    InteropAdapter *interop=nullptr;InteropFactory *vkFactory=nullptr;
    REQUIRE(adapter->QueryInterface(adapterId,(void**)&interop)==S_OK);
    REQUIRE(factory->QueryInterface(factoryId,(void**)&vkFactory)==S_OK);
    VkInstance instance;VkPhysicalDevice physical;PFN_vkGetInstanceProcAddr get;
    interop->GetVulkanHandles(&instance,&physical);vkFactory->GetVulkanInstance(&instance,&get);
    auto propsFn=(PFN_vkGetPhysicalDeviceProperties2)get(instance,"vkGetPhysicalDeviceProperties2");
    REQUIRE(propsFn);
    auto enumerate=(PFN_vkEnumerateDeviceExtensionProperties)get(instance,"vkEnumerateDeviceExtensionProperties");
    REQUIRE(enumerate);uint32_t count=0;REQUIRE(enumerate(physical,nullptr,&count,nullptr)==VK_SUCCESS);
    auto extensions=new VkExtensionProperties[count];
    REQUIRE(enumerate(physical,nullptr,&count,extensions)==VK_SUCCESS);
    bool pciAvailable=false;for(uint32_t i=0;i<count;i++)
        if(!strcmp(extensions[i].extensionName,VK_EXT_PCI_BUS_INFO_EXTENSION_NAME))pciAvailable=true;
    delete[] extensions;
    if(!pciAvailable){puts("UNAVAILABLE software adapter has no VK_EXT_pci_bus_info");return 77;}
    VkPhysicalDevicePCIBusInfoPropertiesEXT pci={};pci.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PCI_BUS_INFO_PROPERTIES_EXT;
    VkPhysicalDeviceProperties2 props={};props.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2;props.pNext=&pci;
    propsFn(physical,&props);
    printf("ADAPTER name=%s PCI=%u:%u:%u.%u\n",props.properties.deviceName,pci.pciDomain,pci.pciBus,pci.pciDevice,pci.pciFunction);
    unsigned values[3]={pci.pciDomain,pci.pciBus,pci.pciDevice};
    if(!strcmp(argv[4],"mismatch"))values[1]^=0x80;
    FILE *file=fopen(argv[1],"wb");REQUIRE(file);REQUIRE(fwrite(values,sizeof(values),1,file)==1);REQUIRE(!fclose(file));
    HMODULE nvapi=LoadLibraryA(argv[2]);REQUIRE(nvapi);
    char loaded[MAX_PATH];REQUIRE(GetModuleFileNameA(nvapi,loaded,sizeof(loaded)));printf("NVAPI module=%s\n",loaded);
    auto query=(void*(__cdecl*)(unsigned))GetProcAddress(nvapi,"nvapi_QueryInterface");REQUIRE(query);
    auto init=(NvAPI_Status(__cdecl*)())query(0x0150e828);
    auto unload=(NvAPI_Status(__cdecl*)())query(0xd22bdd7e);
    auto topology=(NvAPI_Status(__cdecl*)(NV_COMPUTE_GPU_TOPOLOGY*))query(0x5786cc6e);
    auto privateTopology=(NvAPI_Status(__cdecl*)(NV_COMPUTE_GPU_TOPOLOGY*,...))query(0x36e39e6b);
    REQUIRE(init&&unload&&topology&&privateTopology);REQUIRE(init()==NVAPI_OK);
    NV_COMPUTE_GPU_TOPOLOGY_V1 v1={};v1.version=NV_COMPUTE_GPU_TOPOLOGY_VER1;
    int rc1=topology((NV_COMPUTE_GPU_TOPOLOGY*)&v1);
    NV_COMPUTE_GPU_TOPOLOGY v2={};v2.version=NV_COMPUTE_GPU_TOPOLOGY_VER;
    int rc2=topology(&v2);
    NV_COMPUTE_GPU_TOPOLOGY priv={};priv.version=0x12345678;
    int rcPrivate=privateTopology(&priv,0,0,0);
    printf("TOPOLOGY v1=%d count=%u v2=%d count=%u private=%d count=%u\n",rc1,v1.gpuCount,rc2,v2.gpuCount,rcPrivate,priv.gpuCount);
    bool positive=!strcmp(argv[3],"on")&&!strcmp(argv[4],"match");
    REQUIRE(rc1==NVAPI_OK);REQUIRE(v1.gpuCount==(positive?1u:0u));
    if(positive) {
        REQUIRE(v1.computeGpus[0].flags==0xb);
        REQUIRE(rc2==NVAPI_OK&&v2.gpuCount==1&&v2.computeGpus&&v2.computeGpus[0].flags==0x21001b);
        REQUIRE(rcPrivate==NVAPI_OK&&priv.gpuCount==1&&priv.computeGpus==v2.computeGpus&&priv.version==0x12345678);
        HMODULE cuda=GetModuleHandleA("nvcuda.dll");REQUIRE(cuda);
        REQUIRE(GetModuleFileNameA(cuda,loaded,sizeof(loaded)));printf("CUDA module=%s\n",loaded);
        auto attribute=(int(WINAPI*)(int*,int,int))GetProcAddress(cuda,"cuDeviceGetAttribute");REQUIRE(attribute);
        int attributes[3]={50,33,34};for(unsigned i=0;i<3;i++) {
            int actual=-1;REQUIRE(!attribute(&actual,attributes[i],0));REQUIRE(actual==(int)values[i]);
        }
    } else {
        REQUIRE(rc2==NVAPI_INCOMPATIBLE_STRUCT_VERSION);REQUIRE(rcPrivate==NVAPI_NO_IMPLEMENTATION);
    }
    REQUIRE(unload()==NVAPI_OK);puts("PASS provider topology and PCI");return 0;
}
