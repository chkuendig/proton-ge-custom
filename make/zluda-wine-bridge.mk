# CUDA Wine modules are owned by the ZLUDA submodule. The legacy NVIDIA
# nvcuda/nvcuda32 sources and their optional nvidia-libs install tree are gone.
# This target builds only the bridge; the native backend/runtime overlay remains
# the separate packaging/zluda-overlay native stage.
ifeq ($(TARGET_ARCH),x86_64)

$(eval $(call rules-source,nvcuda,$(SRCDIR)/zluda/wine/nvcuda))

NVCUDA_BUILD := $(OBJ)/obj-zluda-wine-bridge
NVCUDA_SDK := $(NVCUDA_BUILD)/wine-sdk
NVCUDA_PAYLOAD := $(NVCUDA_BUILD)/payload
NVCUDA_SCRIPTS := $(SRC)/packaging/zluda-overlay

# Use Wine's prepared source and completed x64 SDK, including native/PE import
# libraries. Waiting for both Wine dist targets prevents their DLL copies from
# overwriting the relocated bridge when make runs with parallel scheduling.
$(OBJ)/.nvcuda-build: $(OBJ)/.nvcuda-post-source $(OBJ)/.wine-x86_64-build
$(OBJ)/.nvcuda-build: $(NVCUDA_SCRIPTS)/build-bridge.sh $(NVCUDA_SCRIPTS)/wine_sdk.py $(NVCUDA_SCRIPTS)/bridge_tools.py

$(OBJ)/.nvcuda-build:
	rm -rf "$(NVCUDA_BUILD)"
	python3 "$(NVCUDA_SCRIPTS)/wine_sdk.py" "$(NVCUDA_BUILD)" "$(NVCUDA_SDK)" "$(NVCUDA_SRC)/build-wine64.txt" \
	    --object "$(WINE_x86_64_OBJ)" --source "$(WINE_SRC)"
	env BRIDGE_SOURCE="$(NVCUDA_SRC)" BUILD_ROOT="$(NVCUDA_BUILD)" BRIDGE_DEST="$(NVCUDA_PAYLOAD)" \
	    WINE_SDK_ROOT="$(NVCUDA_SDK)" JOBS=3 bash "$(NVCUDA_SCRIPTS)/build-bridge.sh"
	touch $@

$(OBJ)/.nvcuda-dist: $(OBJ)/.nvcuda-build $(OBJ)/.wine-x86_64-dist $(OBJ)/.wine-i386-dist
	install -d "$(DST_LIBDIR)/wine/x86_64-unix" "$(DST_LIBDIR)/wine/x86_64-windows" "$(DST_LIBDIR)/wine/i386-windows"
	cp -a "$(NVCUDA_PAYLOAD)/lib/wine/." "$(DST_LIBDIR)/wine/"
	rm -rf "$(DST_LIBDIR)/wine/nvidia-libs/nvcuda"
	install -d "$(DST_BASE)/licenses/nvcuda" "$(DST_BASE)/sources"
	cp "$(NVCUDA_SRC)/LICENSE.md" "$(NVCUDA_SRC)/dlls/nvcuda/wow64/ZLUDA-MIT.txt" "$(DST_BASE)/licenses/nvcuda/"
	git -C "$(SRCDIR)/zluda" archive HEAD:wine/nvcuda | gzip -n > "$(DST_BASE)/sources/nvcuda.tar.gz"
	cp "$(NVCUDA_SCRIPTS)/build-bridge.sh" "$(NVCUDA_SCRIPTS)/wine_sdk.py" "$(NVCUDA_SCRIPTS)/bridge_tools.py" "$(DST_BASE)/sources/"
	touch $@

nvcuda-build: $(OBJ)/.nvcuda-build
nvcuda nvcuda-dist: $(OBJ)/.nvcuda-dist
all-build: nvcuda-build
all all-dist default_pfx: nvcuda-dist
.PHONY: nvcuda nvcuda-build nvcuda-dist

nvcuda-clean::
	rm -rf "$(NVCUDA_BUILD)" "$(OBJ)/.nvcuda-build" "$(OBJ)/.nvcuda-dist"

endif
