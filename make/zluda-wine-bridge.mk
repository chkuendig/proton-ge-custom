# CUDA builtin PE modules + Unixlib are owned by ZLUDA. The backend/runtime
# overlay remains a separate component. No legacy Winelib relay is installed.
ifeq ($(TARGET_ARCH),x86_64)

$(eval $(call rules-source,nvcuda,$(SRCDIR)/zluda/wine/zluda_wine))

NVCUDA_BUILD := $(OBJ)/obj-zluda-wine-bridge
NVCUDA_SDK := $(NVCUDA_BUILD)/wine-sdk
NVCUDA_PAYLOAD := $(NVCUDA_BUILD)/payload
NVCUDA_SCRIPTS := $(SRC)/packaging/zluda-overlay

$(OBJ)/.nvcuda-build: $(OBJ)/.nvcuda-post-source $(OBJ)/.wine-x86_64-build
$(OBJ)/.nvcuda-build: $(NVCUDA_SCRIPTS)/build-bridge.sh $(NVCUDA_SCRIPTS)/wine_sdk.py $(NVCUDA_SCRIPTS)/cuda_package.py

$(OBJ)/.nvcuda-build:
	rm -rf "$(NVCUDA_BUILD)"
	mkdir -p "$(NVCUDA_BUILD)/downloads"
	python3 "$(NVCUDA_SCRIPTS)/inputs.py" bridge-check "$(SRCDIR)"
	python3 "$(NVCUDA_SCRIPTS)/inputs.py" fetch "$(NVCUDA_BUILD)/downloads" --group bridges
	env BUILD_ROOT="$(NVCUDA_BUILD)" DOWNLOAD_ROOT="$(NVCUDA_BUILD)/downloads" BUILD_PART=bridges bash "$(NVCUDA_SCRIPTS)/install-rust.sh"
	python3 "$(NVCUDA_SCRIPTS)/wine_sdk.py" "$(NVCUDA_BUILD)" "$(NVCUDA_SDK)" --generated \
	    --object "$(WINE_x86_64_OBJ)" --source "$(WINE_SRC)"
	# Build from the immutable checkout: path dependencies and Git provenance
	# require the complete ZLUDA source layout, rather than a lone rsynced crate.
	env BRIDGE_SOURCE="$(SRCDIR)/zluda/wine/zluda_wine" BUILD_ROOT="$(NVCUDA_BUILD)" BRIDGE_DEST="$(NVCUDA_PAYLOAD)" \
	    PATH="$(NVCUDA_BUILD)/rust/bin:$(PATH)" CARGO_HOME="$(NVCUDA_BUILD)/cargo" \
	    WINE_SDK_ROOT="$(NVCUDA_SDK)" WINE_SOURCE_ROOT="$(WINE_SRC)" JOBS=3 bash "$(NVCUDA_SCRIPTS)/build-bridge.sh"
	touch $@

# Both Wine dist targets must finish before removing canonical nvcuda files.
$(OBJ)/.nvcuda-dist: $(OBJ)/.nvcuda-build $(OBJ)/.wine-x86_64-dist $(OBJ)/.wine-i386-dist
	install -d "$(DST_LIBDIR)/wine-cuda" "$(DST_BASE)/provenance" "$(DST_BASE)/sources"
	cp -a "$(NVCUDA_PAYLOAD)/files/lib/wine-cuda/." "$(DST_LIBDIR)/wine-cuda/"
	cp -a "$(NVCUDA_PAYLOAD)/provenance/." "$(DST_BASE)/provenance/"
	python3 "$(NVCUDA_SCRIPTS)/cuda_package.py" archive-stubs "$(DST_BASE)" --archive "$(NVCUDA_BUILD)/downloads/GE-Proton11-7-x86_64.tar.gz"
	python3 "$(NVCUDA_SCRIPTS)/cuda_package.py" clean "$(DST_BASE)"
	env CARGO_HOME="$(NVCUDA_BUILD)/cargo" python3 "$(NVCUDA_SCRIPTS)/cuda_package.py" licenses "$(DST_BASE)" --source "$(SRCDIR)/zluda"
	env CARGO_HOME="$(NVCUDA_BUILD)/cargo" PATH="$(NVCUDA_BUILD)/rust/bin:$(PATH)" WINE_SOURCE_ROOT="$(WINE_SRC)" WINE_SDK_ROOT="$(NVCUDA_SDK)" \
	    python3 "$(NVCUDA_SCRIPTS)/source_archive.py" --source "$(SRCDIR)/zluda" \
	    --build "$(NVCUDA_BUILD)/zluda-wine/target" --output "$(DST_BASE)/sources/zluda-wine.tar.gz"
	rm -rf "$(DST_BASE)/sources/overlay-recipes"
	mkdir -p "$(DST_BASE)/sources/overlay-recipes"
	git -C "$(SRCDIR)" archive HEAD packaging/zluda-overlay | tar -x --strip-components=2 -C "$(DST_BASE)/sources/overlay-recipes"
	touch $@

nvcuda-build: $(OBJ)/.nvcuda-build
nvcuda nvcuda-dist: $(OBJ)/.nvcuda-dist
all-build: nvcuda-build
all all-dist default_pfx: nvcuda-dist
.PHONY: nvcuda nvcuda-build nvcuda-dist

nvcuda-clean::
	rm -rf "$(NVCUDA_BUILD)" "$(OBJ)/.nvcuda-build" "$(OBJ)/.nvcuda-dist"

endif
