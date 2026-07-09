################################################################################
# Automatically-generated file. Do not edit!
################################################################################

SHELL = cmd.exe

# Each subdirectory must supply rules for building sources it contributes
%.obj: ../%.c $(GEN_OPTS) | $(GEN_FILES) $(GEN_MISC_FILES)
	@echo 'Arm Compiler - building file: "$<"'
	"C:/ti/ti-cgt-arm_16.9.6.LTS/bin/armcl" -mv7R4 --code_state=16 --float_support=VFPv3D16 -me -O3 --include_path="C:/Users/pc/Desktop/vital_sign/firmware/vital_signs_tracking_mss" --include_path="C:/ti/mmwave_sdk_03_06_02_00-LTS" --include_path="C:/ti/mmwave_sdk_03_06_02_00-LTS/packages" --include_path="C:/Users/pc/Desktop/vital_sign/firmware/vital_signs_tracking_mss/mss" --include_path="C:/Users/pc/Desktop/vital_sign/firmware/vital_signs_tracking_mss/common" --include_path="C:/ti/ti-cgt-arm_16.9.6.LTS/include" --define=_LITTLE_ENDIAN --define=SOC_XWR68XX --define=SUBSYS_MSS --define=DOWNLOAD_FROM_CCS --define=MMWAVE_L3RAM_SIZE=0xC0000 --define=DebugP_ASSERT_ENABLED -g --c99 --diag_warning=225 --diag_wrap=off --display_error_number --gen_func_subsections=on --enum_type=int --abi=eabi --preproc_with_compile --preproc_dependency="$(basename $(<F)).d_raw" $(GEN_OPTS__FLAG) "$<"
	@echo 'Finished building: "$<"'
	@echo ' '

build-1736427767:
	@$(MAKE) --no-print-directory -Onone -f subdir_rules.mk build-1736427767-inproc

build-1736427767-inproc: ../mss_mmw.cfg
	@echo 'XDCtools - building file: "$<"'
	"C:/ti/xdctools_3_50_08_24_core/xs" --xdcpath="C:/ti/bios_6_73_01_01/packages;" xdc.tools.configuro -o configPkg -t ti.targets.arm.elf.R4F -p ti.platforms.cortexR:IWR68XX:false:200 -r release -c "C:/ti/ti-cgt-arm_16.9.6.LTS" --compileOptions "--enum_type=int " "$<"
	@echo 'Finished building: "$<"'
	@echo ' '

configPkg/linker.cmd: build-1736427767 ../mss_mmw.cfg
configPkg/compiler.opt: build-1736427767
configPkg: build-1736427767


