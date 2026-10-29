# Configure-free target fixture; see generate_wine_makefiles.py
# Wine pin: 46b29104e3741fe23bf5e2547196a253aab88c89
all: include/all tools/winebuild/all tools/winegcc/all tools/widl/all tools/wrc/all tools/wmc/all dlls/kernel32/all dlls/ntdll/all server/all programs/wineboot/all
include/access.h:
include/all: include/access.h
tools/winebuild/winebuild:
tools/winebuild/all: tools/winebuild/winebuild
tools/winegcc/winegcc:
tools/winegcc/all: tools/winegcc/winegcc
tools/widl/widl:
tools/widl/all: tools/widl/widl
tools/wrc/wrc:
tools/wrc/all: tools/wrc/wrc
tools/wmc/wmc:
tools/wmc/all: tools/wmc/wmc
dlls/winecrt0/i386-windows/libwinecrt0.a:
dlls/kernel32/i386-windows/libkernel32.a:
dlls/kernel32/i386-windows/kernel32.dll:
dlls/kernel32/all: dlls/kernel32/i386-windows/kernel32.dll
dlls/ntdll/i386-windows/libntdll.a:
dlls/ntdll/i386-windows/ntdll.dll:
dlls/ntdll/ntdll.so:
dlls/ntdll/all: dlls/ntdll/i386-windows/ntdll.dll dlls/ntdll/ntdll.so
server/wineserver:
server/all: server/wineserver
programs/wineboot/i386-windows/wineboot.exe:
programs/wineboot/all: programs/wineboot/i386-windows/wineboot.exe
