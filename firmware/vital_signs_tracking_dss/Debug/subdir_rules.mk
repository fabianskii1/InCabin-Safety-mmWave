################################################################################
# Automatically-generated file. Do not edit!
################################################################################

SHELL = cmd.exe

# Each subdirectory must supply rules for building sources it contributes
%.oe674: ../%.c $(GEN_OPTS) | $(GEN_FILES) $(GEN_MISC_FILES)
	@echo 'C6000 Compiler - building file: "$<"'
	"C:/ti/ti-cgt-c6000_8.3.3/bin/cl6x" -mv6740 --abi=eabi -O3 --opt_for_speed=3 --include_path="C:/Users/pc/Desktop/vital_sign/firmware/vital_signs_tracking_dss" --include_path="C:/ti/mmwave_sdk_03_06_02_00-LTS/packages" --include_path="C:/ti/mathlib_c674x_3_1_2_1/packages" --include_path="C:/ti/dsplib_c64Px_3_4_0_0/packages/ti/dsplib/src/DSP_fft16x16/c64P" --include_path="C:/ti/dsplib_c64Px_3_4_0_0/packages/ti/dsplib/src/DSP_fft32x32/c64P" --include_path="C:/Users/pc/Desktop/vital_sign/firmware/vital_signs_tracking_dss/dss" --include_path="C:/Users/pc/Desktop/vital_sign/firmware/vital_signs_tracking_dss/common" --include_path="C:/ti/mathlib_c674x_3_1_2_1/packages" --include_path="C:/ti/dsplib_c64Px_3_4_0_0/packages/" --include_path="C:/ti/dsplib_c64Px_3_4_0_0/packages/ti/dsplib/src/DSP_fft16x16/c64P" --include_path="C:/ti/dsplib_c64Px_3_4_0_0/packages/ti/dsplib/src/DSP_fft32x32/c64P" --include_path="C:/ti/ti-cgt-c6000_8.3.3/include" --define=SOC_XWR68XX --define=SUBSYS_DSS --define=MMWAVE_L3RAM_SIZE=0xC0000 --define=MMWAVE_L3RAM_NUM_BANK=6 --define=MMWAVE_SHMEM_BANK_SIZE=0x20000 --define=DOWNLOAD_FROM_CCS --define=DebugP_ASSERT_ENABLED -g --gcc --diag_warning=225 --diag_wrap=off --display_error_number --gen_func_subsections=on --obj_extension=.oe674 --preproc_with_compile --preproc_dependency="$(basename $(<F)).d_raw" $(GEN_OPTS__FLAG) "$<"
	@echo 'Finished building: "$<"'
	@echo ' '

build-1550912563:
	@$(MAKE) --no-print-directory -Onone -f subdir_rules.mk build-1550912563-inproc

build-1550912563-inproc: ../dss_mmw.cfg
	@echo 'XDCtools - building file: "$<"'
	"C:/ti/xdctools_3_50_08_24_core/xs" --xdcpath="C:/ti/bios_6_73_01_01/packages;" xdc.tools.configuro -o configPkg -t ti.targets.elf.C674 -p ti.platforms.c6x:IWR68XX:false:600 -r debug -c "C:/ti/ti-cgt-c6000_8.3.3" "$<"
	@echo 'Finished building: "$<"'
	@echo ' '

configPkg/linker.cmd: build-1550912563 ../dss_mmw.cfg
configPkg/compiler.opt: build-1550912563
configPkg: build-1550912563


