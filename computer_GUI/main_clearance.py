# 시트 폴딩 안전(끼임 방지)용 존 clearance 판정 진입점.
# main_occupancy.py 와 동일한 TLV 파싱 위에 clearance 상태머신을 얹은 버전.
#
# 사용법: python main_clearance.py <userPort> <dataPort> <configFile>

import config
import serialhelper
import utils
import clearance
import plot_clearance as plot

import argparse
import numpy as np
from datetime import datetime
import time


def main():
    parser = argparse.ArgumentParser(description='Seat-fold clearance monitor (anti-entrapment): per-zone EMPTY/OCCUPIED/UNKNOWN decision')
    parser.add_argument('userPort', help='user serial port of the TI chip')
    parser.add_argument('dataPort', help='data serial port of the TI chip')
    parser.add_argument('configFile', help='the config file send to the TI chip')
    args = parser.parse_args()

    cfgFile = config.read_config_file(args.configFile)
    cfgFileParsed = config.parse_config_file(cfgFile)

    serialUser = serialhelper.SerialHelper(args.userPort, 115200)
    serialData = serialhelper.SerialHelper(args.dataPort, 921600)

    serialUser.sendConfig(cfgFile)

    numZones = cfgFileParsed.get('numZones', 0)
    fps = 1000.0 / cfgFileParsed['framePeriodicity']
    monitor = clearance.ClearanceMonitor(numZones, fps)
    plots = plot.PlotHelper(cfgFileParsed.get('zoneDef'), numZones)

    zoneDef = cfgFileParsed.get('zoneDef')
    databuffer = bytearray()
    startTime = time.time()
    extractedValue = np.zeros(20)
    updatedZones = np.zeros((4, 2))
    personsDetected = 0
    rangeAzimuth = np.zeros((cfgFileParsed['numRangeBins'], cfgFileParsed['numAngleBins']))
    stateLog = []  # (time, 존별 state 코드) CSV 로그

    stateCode = {clearance.STATE_UNKNOWN: -1, clearance.STATE_EMPTY: 0, clearance.STATE_OCCUPIED: 1}

    try:
        # Main loop
        while True:
            newdata = serialData.readall()
            newDebug = serialUser.readall()
            if len(newDebug) != 0:
                print(str(newDebug, 'utf-8'))
            if newdata is None:
                continue
            databuffer += newdata

            magicNumber = False
            magicIdx = databuffer.find(b'\x02\x01\x04\x03\x06\x05\x08\x07')
            if magicIdx != -1:
                databuffer = databuffer[magicIdx:]

                totalPacketLength = int.from_bytes(databuffer[8:12], 'little')
                if len(databuffer) >= totalPacketLength:
                    magicNumber = True
            if magicNumber:
                header, index = utils.getHeader(databuffer, 0)

                for i in range(header['numTLVs']):
                    tlv, index = utils.getTlv(databuffer, index)

                    # MMWDEMO_UART_MSG_OD_DEMO_RANGE_AZIMUT_HEAT_MAP
                    if tlv['type'] == 8:
                        if cfgFileParsed['rangeAzimuthHeatMap'] == 32:
                            rangeAzimuth, index = utils.getOccupDemoRangeAzimuthHeatMap(databuffer, index, cfgFileParsed['numRangeBins'], cfgFileParsed['numAngleBins'])
                        elif cfgFileParsed['rangeAzimuthHeatMap'] == 16:
                            rangeAzimuth, index = utils.getOccupDemoShortHeatMap(databuffer, index, cfgFileParsed['numRangeBins'], cfgFileParsed['numAngleBins'])
                        elif cfgFileParsed['rangeAzimuthHeatMap'] == 8:
                            rangeAzimuth, index = utils.getOccupDemoByteHeatMap(databuffer, index, cfgFileParsed['numRangeBins'], cfgFileParsed['numAngleBins'])
                    # MMWDEMO_UART_MSG_OD_DEMO_DECISION (이 포크에선 CPD 비활성 — 피크 매핑으로 대체)
                    elif tlv['type'] == 9:
                        _decision, index = utils.getOccupDemoDecision(databuffer, index, cfgFileParsed['numZones'])
                    # VS_OUTPUT_HEART_BREATHING_RATES
                    elif tlv['type'] == 10:
                        extractedValue, index = utils.getVitalSignsDemoHeartBreathingRate(databuffer, index)
                    # MMWDEMO_UART_MSG_OD_ROW_NOISE
                    elif tlv['type'] == 11:
                        index = utils.dumpRowNoiseValues(databuffer, index, 64)
                    elif tlv['type'] == 12:
                        personsDetected, updatedZones, index = utils.getUpdatedZones(databuffer, index)
                    # MMWDEMO_UART_MSG_STATS
                    elif tlv['type'] == 6:
                        statsInfo, index = utils.getStatsInfo(databuffer, index)
                        print(statsInfo)
                    else:
                        print('Unprocessed TLV:', tlv['type'])

                # 피크 슬롯(방위 순) → zoneDef 좌석으로 재매핑 (히트맵 품질+안정성 필터)
                seatDecision, seatVitals = clearance.remap_peaks_to_seats(
                    zoneDef, updatedZones, personsDetected, extractedValue,
                    heatmap=rangeAzimuth, peakFilter=monitor.peakFilter)

                now = time.time() - startTime
                results = monitor.update(seatDecision, seatVitals, now=now)

                # ECU 연동 지점: 전 존 EMPTY 확정일 때만 폴딩 허용 신호
                foldOk = monitor.all_fold_permitted()

                stateLog.append([now] + [stateCode[r['state']] for r in results])
                plots.update(rangeAzimuth, seatVitals, results, foldOk, updatedZones, personsDetected)
                databuffer = databuffer[index:]
            else:
                # 패킷 대기 중에도 GUI 응답 유지
                plots.process_events()
                time.sleep(0.001)
    except KeyboardInterrupt:
        np.savetxt('clearance_log_' + datetime.now().strftime('%Y-%m-%d_%H-%M-%S') + '.csv',
                   np.array(stateLog), delimiter=',', fmt='%.2f',
                   header='time,' + ','.join('zone{}'.format(i) for i in range(numZones)))


if __name__ == '__main__':
    main()
