# Patched TI VitalSigns: Breathing / Hold / Warning / Motion
import csv
import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from Demo_Classes.people_tracking import PeopleTracking

from gui_common import median
from demo_defines import DEVICE_DEMO_DICT

from PySide2.QtWidgets import QGroupBox, QGridLayout, QLabel, QWidget
from PySide2.QtGui import QFont
import pyqtgraph as pg
from PySide2.QtCore import Qt

MAX_VITALS_PATIENTS = 2
NUM_FRAMES_PER_VITALS_PACKET = 15
NUM_VITALS_FRAMES_IN_PLOT = 150
NUM_HEART_RATES_FOR_MEDIAN = 10
NUM_VITALS_FRAMES_IN_PLOT_IWRL6432 = 15
HOLD_DEVIATION = 0.025
HOLD_EXIT_DEV = 0.04
HOLD_BREATH_MAX = 0.10
HOLD_ENTER_PACKETS = 2
HOLD_EXIT_PACKETS = 3
DEV_SMOOTH_N = 3
# Chip deviation is 0 until the breath window starts filling. Do not Hold yet.
HOLD_ARM_SEC = 5.0
HOLD_PTP_FLAT = 0.5
HOLD_PTP_JUMP = 1.0
HOLD_PTP_RATIO = 5.0
MOTION_SPEED = 0.40
MOTION_QUIET_FRAMES = 10
# ~3 s at 90 ms frames; after this, held averages are treated as stale.
MOTION_STALE_FRAMES = 33
HOLD_WARNING_SEC = 10.0
# After this with no track, clear vitals. Short enough to drop an empty seat,
# long enough that Hold is not wiped by a 1-2 s track drop.
EMPTY_CLEAR_SEC = 3.0
LOG_DIR = Path(__file__).resolve().parents[1] / 'logs'
TWIN_URL = 'http://127.0.0.1:8766/event'
HR_AVG_SEC = 20.0
LOG_FIELDS = [
    'time', 'elapsed_sec', 'hr_window', 'hr_avg_20s',
    'status', 'heart_rate', 'breath_rate', 'deviation',
    'range_bin', 'num_tracks', 'speed_mps', 'pos_x', 'pos_y', 'pos_z',
]


class VitalSigns(PeopleTracking):
    def __init__(self):
        PeopleTracking.__init__(self)
        self.hearPlotData = []
        self.breathPlotData = []
        self.vitalsDict = None
        self.numTracks = None
        self.vitalsPatientData = []
        self.xWRLx432 = False
        self.vitals = []
        self.inMotion = False
        self.motionQuiet = 0
        self.motionFrames = 0
        self.holdStarted = None
        self.holdExitCount = 0
        self.holdEnterCount = 0
        self.holdArmed = False
        self.vitalsSince = None
        self.holdWavePtp = None
        self.emptySince = None
        self._dev_hist = []
        self._dev_smooth = None
        self._csv_fp = None
        self._csv_writer = None
        self._csv_t0 = None
        self._hr_win = 0
        self._hr_win_vals = []
        self._dev_chip_raw = None
        self._twin_warned = False
        self._twin_next = 0.0
        self._twin_hr = 0
        self._twin_rr = 0

    def setupGUI(self, gridLayout, demoTabs, device):
        PeopleTracking.setupGUI(self, gridLayout, demoTabs, device)

        self.xWRLx432 = DEVICE_DEMO_DICT[device]["isxWRLx432"]

        self.initVitalsPlots()

        gridLayout.addWidget(self.vitalsPane, 0, 2, 8, 1)

    def initVitalsPlots(self):
        self.vitalsPane = QGroupBox('Vital Signs')
        vitalsPaneLayout = QGridLayout()
        self.vitals = []

        for i in range(MAX_VITALS_PATIENTS):
            patientDict = {}
            patientName = 'Patient' + str(i+1)

            patientPane = QGroupBox(patientName)
            patientPaneLayout = QGridLayout()

            statusLabel = QLabel('Patient Status:')
            breathLabel = QLabel('Breath Rate:')
            heartLabel = QLabel('Heart Rate:')
            rangeBinLabel = QLabel('Range Bin:')
            deviationLabel = QLabel('Deviation:')

            patientDict['plot'] = pg.PlotWidget()
            patientDict['plot'].setBackground('w')
            patientDict['plot'].showGrid(x=True,y=True)
            patientDict['plot'].invertX(True)

            if(self.xWRLx432 == 1):
                patientDict['plot'].setXRange(0, NUM_VITALS_FRAMES_IN_PLOT_IWRL6432, padding=0.01)
                patientDict['plot'].setYRange(0,120,padding=0.1)
                patientDict['plot'].getPlotItem().setLabel('left', 'Hear Rate and Breath Rate per minute')
                patientDict['plot'].getPlotItem().setLabel('bottom', 'Vital Signs Frame Number')
            else:
                patientDict['plot'].setXRange(0,NUM_VITALS_FRAMES_IN_PLOT,padding=0.01)
                patientDict['plot'].setYRange(-1,1,padding=0.1)

            patientDict['plot'].setMouseEnabled(False,False)
            patientDict['heartGraph'] = pg.PlotCurveItem(pen=pg.mkPen(width=3, color='r'))
            patientDict['breathGraph'] = pg.PlotCurveItem(pen=pg.mkPen(width=3, color='b'))
            patientDict['plot'].addItem(patientDict['heartGraph'])
            patientDict['plot'].addItem(patientDict['breathGraph'])

            patientDict['breathRate'] = QLabel('Undefined')
            patientDict['heartRate'] = QLabel('Undefined')
            patientDict['status'] = QLabel('')
            patientDict['rangeBin'] = QLabel('Undefined')
            patientDict['deviation'] = QLabel('-')
            patientDict['name'] = patientName

            labelFont = QFont('Arial', 16)
            labelFont.setBold(True)
            dataFont = (QFont('Arial', 12))
            heartLabel.setFont(labelFont)
            breathLabel.setFont(labelFont)
            statusLabel.setFont(labelFont)
            rangeBinLabel.setFont(labelFont)
            deviationLabel.setFont(labelFont)
            patientDict['breathRate'].setStyleSheet('color: blue')
            patientDict['heartRate'].setStyleSheet('color: red')
            patientDict['status'].setFont(dataFont)
            patientDict['breathRate'].setFont(dataFont)
            patientDict['heartRate'].setFont(dataFont)
            patientDict['rangeBin'].setFont(dataFont)
            patientDict['deviation'].setFont(dataFont)

            patientPaneLayout.addWidget(patientDict['plot'],2,0,1,5)
            patientPaneLayout.addWidget(statusLabel,0,0,alignment=Qt.AlignHCenter)
            patientPaneLayout.addWidget(patientDict['status'],1,0,alignment=Qt.AlignHCenter)
            patientPaneLayout.addWidget(breathLabel,0,1,alignment=Qt.AlignHCenter)
            patientPaneLayout.addWidget(patientDict['breathRate'],1,1,alignment=Qt.AlignHCenter)
            patientPaneLayout.addWidget(heartLabel,0,2,alignment=Qt.AlignHCenter)
            patientPaneLayout.addWidget(patientDict['heartRate'],1,2,alignment=Qt.AlignHCenter)
            patientPaneLayout.addWidget(rangeBinLabel,0,3,alignment=Qt.AlignHCenter)
            patientPaneLayout.addWidget(patientDict['rangeBin'],1,3,alignment=Qt.AlignHCenter)
            patientPaneLayout.addWidget(deviationLabel,0,4,alignment=Qt.AlignHCenter)
            patientPaneLayout.addWidget(patientDict['deviation'],1,4,alignment=Qt.AlignHCenter)

            patientPane.setLayout(patientPaneLayout)
            patientDict['pane'] = patientPane

            self.vitals.append(patientDict)

            if (i != 0):
                patientPane.setVisible(False)

            vitalsPaneLayout.addWidget(patientPane,i,0)

        self.vitalsPane.setLayout(vitalsPaneLayout)

    def _reset_motion(self):
        self.inMotion = False
        self.motionQuiet = 0
        self.motionFrames = 0

    def _reset_hold(self):
        self.holdStarted = None
        self.holdExitCount = 0
        self.holdEnterCount = 0
        self.holdWavePtp = None

    def _reset_hold_arm(self):
        self.holdArmed = False
        self.vitalsSince = None
        self._dev_hist = []
        self._dev_smooth = None
        self._dev_chip_raw = None
        self._reset_hold()

    def _smooth_deviation(self, deviation):
        self._dev_hist.append(float(deviation))
        while len(self._dev_hist) > DEV_SMOOTH_N:
            self._dev_hist.pop(0)
        self._dev_smooth = sum(self._dev_hist) / len(self._dev_hist)
        return self._dev_smooth

    def _show_empty(self):
        self._reset_hold_arm()
        self.emptySince = None
        self.numTracks = 0
        for i in range(len(self.vitals)):
            if i != 0 and not self.vitals[i]['pane'].isVisible():
                continue
            pane = self.vitals[i]
            pane['status'].setText('')
            pane['status'].setStyleSheet('')
            pane['breathRate'].setText('')
            pane['heartRate'].setText('')
            pane['rangeBin'].setText('')
            pane['deviation'].setText('-')
            pane['deviation'].setStyleSheet('')
            pane['heartGraph'].setData([])
            pane['breathGraph'].setData([])
            if i < len(self.vitalsPatientData):
                self.vitalsPatientData[i]['heartWaveform'] = []
                self.vitalsPatientData[i]['breathWaveform'] = []
                self.vitalsPatientData[i]['heartRate'] = []
                self.vitalsPatientData[i]['breathRateHist'] = []
                self.vitalsPatientData[i]['breathRate'] = 0
                self.vitalsPatientData[i]['breathDeviation'] = 0

    def _in_hold(self, patientId=0):
        if not self.vitals:
            return False
        return self.vitals[patientId]['status'].text() in ('Hold', 'Warning')

    def _packet_ptp(self):
        if self.vitalsDict is None:
            return 0.0
        wf = self.vitalsDict.get('breathWaveform')
        if wf is None or len(wf) == 0:
            return 0.0
        return float(max(wf) - min(wf))

    def _is_unwrap_jump(self, deviation):
        if deviation >= HOLD_BREATH_MAX:
            return True
        ptp = self._packet_ptp()
        last = self.holdWavePtp
        if last is not None and last < HOLD_PTP_FLAT and ptp >= max(HOLD_PTP_JUMP, last * HOLD_PTP_RATIO):
            return True
        return False

    def _classify_breath(self, deviation, patientId=0):
        low = deviation < HOLD_DEVIATION
        breathing_again = HOLD_EXIT_DEV <= deviation < HOLD_BREATH_MAX
        if self.vitalsSince is None:
            self.vitalsSince = time.monotonic()
        if breathing_again or (time.monotonic() - self.vitalsSince >= HOLD_ARM_SEC):
            self.holdArmed = True
        if self._in_hold(patientId) or self.holdStarted is not None:
            if low:
                self.holdExitCount = 0
                return self._tick_hold_warning(), False
            if self._is_unwrap_jump(deviation):
                return self._tick_hold_warning(), True
            if breathing_again:
                self.holdExitCount += 1
                if self.holdExitCount >= HOLD_EXIT_PACKETS:
                    self._reset_hold()
                    return 'Breathing', False
                return self._tick_hold_warning(), False
            self.holdExitCount = 0
            return self._tick_hold_warning(), False
        if not self.holdArmed:
            self.holdEnterCount = 0
            return 'Breathing', False
        if low:
            self.holdEnterCount += 1
            if self.holdEnterCount >= HOLD_ENTER_PACKETS:
                return self._tick_hold_warning(), False
            return 'Breathing', False
        self.holdEnterCount = 0
        return 'Breathing', False

    def _set_status(self, patientId, text):
        pane = self.vitals[patientId]
        pane['status'].setText(text)
        pane['status'].setStyleSheet('color: #d9480f' if text == 'Warning' else '')

    def _set_deviation(self, patientId, chip_dev):
        if patientId >= len(self.vitals):
            return
        pane = self.vitals[patientId]
        if chip_dev is None:
            pane['deviation'].setText('-')
            pane['deviation'].setStyleSheet('')
            return
        pane['deviation'].setText('{:.4f}'.format(float(chip_dev)))
        pane['deviation'].setStyleSheet(
            'color: #d9480f' if float(chip_dev) < HOLD_DEVIATION else ''
        )

    def _tick_hold_warning(self):
        now = time.monotonic()
        if self.holdStarted is None:
            self.holdStarted = now
        if now - self.holdStarted >= HOLD_WARNING_SEC:
            return 'Warning'
        return 'Hold'

    def _hold_while_waiting(self):
        if not self.vitals:
            return
        last = self.vitals[0]['status'].text()
        if last in ('Hold', 'Warning'):
            self._set_status(0, self._tick_hold_warning())

    def _start_csv(self):
        if self._csv_fp is not None:
            try:
                self._csv_fp.close()
            except Exception:
                pass
            self._csv_fp = None
            self._csv_writer = None
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        name = datetime.now().strftime('vitals_%Y%m%d_%H%M%S.csv')
        path = LOG_DIR / name
        self._csv_fp = path.open('w', newline='', encoding='utf-8')
        self._csv_writer = csv.DictWriter(self._csv_fp, fieldnames=LOG_FIELDS)
        self._csv_writer.writeheader()
        self._csv_fp.flush()
        self._csv_t0 = time.monotonic()
        self._hr_win = 0
        self._hr_win_vals = []

    def _parse_log_hr(self, text):
        try:
            hr = float(text)
        except (TypeError, ValueError):
            return None
        if hr <= 0:
            return None
        return hr

    def _hr_window_label(self, win_idx):
        start = int(win_idx * HR_AVG_SEC)
        end = int((win_idx + 1) * HR_AVG_SEC)
        return '{}-{}'.format(start, end)

    def _hr_window_avg(self):
        if not self._hr_win_vals:
            return ''
        return '{:.1f}'.format(sum(self._hr_win_vals) / len(self._hr_win_vals))

    def _track_xyz(self, outputDict):
        tracks = outputDict.get('trackData')
        n = int(outputDict.get('numDetectedTracks') or 0)
        if tracks is None or n <= 0:
            return '', '', ''
        return (
            '{:.3f}'.format(float(tracks[0, 1])),
            '{:.3f}'.format(float(tracks[0, 2])),
            '{:.3f}'.format(float(tracks[0, 3])),
        )

    def _write_csv(self, outputDict):
        if self._csv_writer is None or not self.vitals:
            return
        try:
            pane = self.vitals[0]
            ntracks = int(self.numTracks or 0)
            speed = self._track_speed(outputDict) if ntracks > 0 else 0.0
            x, y, z = self._track_xyz(outputDict)
            deviation = ''
            if self._dev_chip_raw is not None:
                deviation = '{:.4f}'.format(self._dev_chip_raw)
            elif self.vitalsDict is not None:
                deviation = '{:.4f}'.format(float(self.vitalsDict['breathDeviation']))
            elapsed = 0.0 if self._csv_t0 is None else (time.monotonic() - self._csv_t0)
            win = int(elapsed // HR_AVG_SEC)
            while self._hr_win < win:
                self._hr_win += 1
                self._hr_win_vals = []
            hr = self._parse_log_hr(pane['heartRate'].text())
            if hr is not None:
                self._hr_win_vals.append(hr)
            self._csv_writer.writerow({
                'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
                'elapsed_sec': '{:.2f}'.format(elapsed),
                'hr_window': self._hr_window_label(win),
                'hr_avg_20s': self._hr_window_avg(),
                'status': pane['status'].text(),
                'heart_rate': pane['heartRate'].text(),
                'breath_rate': pane['breathRate'].text(),
                'deviation': deviation,
                'range_bin': pane['rangeBin'].text(),
                'num_tracks': ntracks,
                'speed_mps': '{:.3f}'.format(speed),
                'pos_x': x,
                'pos_y': y,
                'pos_z': z,
            })
            self._csv_fp.flush()
        except Exception:
            pass

    def _track_speed(self, outputDict):
        tracks = outputDict.get('trackData')
        n = int(outputDict.get('numDetectedTracks') or 0)
        if tracks is None or n <= 0:
            return 0.0
        speed = 0.0
        for i in range(min(n, len(tracks))):
            vx, vy, vz = float(tracks[i, 4]), float(tracks[i, 5]), float(tracks[i, 6])
            mag = (vx * vx + vy * vy + vz * vz) ** 0.5
            if mag > speed:
                speed = mag
        return speed

    def _update_motion_gate(self, speed):
        if speed >= MOTION_SPEED:
            self.inMotion = True
            self.motionQuiet = 0
            self.motionFrames += 1
        else:
            if self.inMotion:
                self.motionFrames += 1
            self.motionQuiet += 1
            if self.motionQuiet >= MOTION_QUIET_FRAMES:
                self.inMotion = False
                self.motionFrames = 0
        return self.inMotion

    def _append_waveforms(self, patientId):
        plot_len = NUM_VITALS_FRAMES_IN_PLOT_IWRL6432 if self.xWRLx432 == 1 else NUM_VITALS_FRAMES_IN_PLOT
        data = self.vitalsPatientData[patientId]
        data['heartWaveform'].extend(self.vitalsDict['heartWaveform'])
        while len(data['heartWaveform']) > plot_len:
            data['heartWaveform'].pop(0)
        data['breathWaveform'].extend(self.vitalsDict['breathWaveform'])
        while len(data['breathWaveform']) > plot_len:
            data['breathWaveform'].pop(0)
        heartWaveform = data['heartWaveform'].copy()
        heartWaveform.reverse()
        breathWaveform = data['breathWaveform'].copy()
        breathWaveform.reverse()
        self.vitals[patientId]['heartGraph'].setData(heartWaveform)
        self.vitals[patientId]['breathGraph'].setData(breathWaveform)

    def _mean_rate(self, values):
        good = [float(v) for v in values if v is not None and float(v) > 0]
        if not good:
            return None
        return sum(good) / len(good)

    def _clear_motion_waveforms(self):
        self.vitals[0]['heartGraph'].setData([])
        self.vitals[0]['breathGraph'].setData([])
        if self.vitalsPatientData:
            self.vitalsPatientData[0]['heartWaveform'] = []
            self.vitalsPatientData[0]['breathWaveform'] = []

    def _show_motion_hold(self):
        self._reset_hold()
        self._set_status(0, 'Motion')
        if self.motionFrames >= MOTION_STALE_FRAMES:
            self.vitals[0]['heartRate'].setText('N/A')
            self.vitals[0]['breathRate'].setText('N/A')
            self._clear_motion_waveforms()
            return
        data = self.vitalsPatientData[0] if self.vitalsPatientData else None
        if data is None:
            self.vitals[0]['heartRate'].setText('N/A')
            self.vitals[0]['breathRate'].setText('N/A')
            return
        hr = self._mean_rate(data.get('heartRate') or [])
        rr = self._mean_rate(data.get('breathRateHist') or [])
        self.vitals[0]['heartRate'].setText('N/A' if hr is None else str(round(hr, 1)))
        self.vitals[0]['breathRate'].setText('N/A' if rr is None else str(round(rr, 1)))

    def _parse_rate_text(self, text):
        try:
            val = float(text)
        except (TypeError, ValueError):
            return None
        if val <= 0:
            return None
        return val

    def _twin_payload(self):
        if not self.vitals:
            return None
        pane = self.vitals[0]
        label = pane['status'].text() or ''
        ntracks = int(self.numTracks or 0)
        empty = (label == '') and ntracks == 0
        hr = self._parse_rate_text(pane['heartRate'].text())
        rr = self._parse_rate_text(pane['breathRate'].text())
        if hr is not None:
            self._twin_hr = int(round(hr))
        if rr is not None:
            self._twin_rr = int(round(rr))
        apnea_sec = 0.0
        if label in ('Hold', 'Warning') and self.holdStarted is not None:
            apnea_sec = max(0.0, time.monotonic() - self.holdStarted)
        detail = label or '검색 중'
        if self._dev_chip_raw is not None:
            detail = '{}  dev={:.4f}'.format(detail, self._dev_chip_raw)
        status = 'SEARCHING' if empty else (label.upper() if label else 'NORMAL')
        return {
            'source': 'vital_signs',
            'status': status,
            'present': (not empty) and ntracks > 0,
            'rr': 0 if empty else self._twin_rr,
            'hr': 0 if empty else self._twin_hr,
            'apnea_sec': round(apnea_sec, 1),
            'detail': detail,
        }

    def _publish_twin(self):
        now = time.monotonic()
        if self._twin_warned and now < self._twin_next:
            return
        payload = self._twin_payload()
        if payload is None:
            return
        body = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            TWIN_URL, data=body, method='POST',
            headers={'Content-Type': 'application/json'})
        try:
            urllib.request.urlopen(req, timeout=0.05)
            self._twin_warned = False
        except (urllib.error.URLError, TimeoutError, OSError):
            if not self._twin_warned:
                print('Digital twin: server not running (python digital_twin/server.py)')
                self._twin_warned = True
            self._twin_next = now + 2.0

    def updateGraph(self, outputDict):
        self.vitalsDict = None
        PeopleTracking.updateGraph(self, outputDict)
        try:
            self._update_vitals(outputDict)
        finally:
            self._write_csv(outputDict)
            self._publish_twin()

    def _update_vitals(self, outputDict):

        if 'vitals' in outputDict:
            self.vitalsDict = outputDict['vitals']

        track_reported = 'numDetectedTracks' in outputDict
        if track_reported:
            self.numTracks = outputDict['numDetectedTracks']

        ntracks = int(self.numTracks or 0)
        max_tracks = getattr(self, 'maxTracks', 1)

        if self.vitalsDict is not None:
            self._dev_chip_raw = float(self.vitalsDict['breathDeviation'])
            self._set_deviation(0, self._dev_chip_raw)

        if track_reported and ntracks == 0:
            self._reset_motion()
            if self.emptySince is None:
                self.emptySince = time.monotonic()
            if time.monotonic() - self.emptySince >= EMPTY_CLEAR_SEC:
                self._show_empty()
                return
            self._hold_while_waiting()
            return
        elif track_reported:
            self.emptySince = None
            moving = self._update_motion_gate(self._track_speed(outputDict))
            if moving:
                self._show_motion_hold()
                return

        if self.vitalsDict is None or len(self.vitalsPatientData) == 0:
            self._hold_while_waiting()
            return

        patientId = self.vitalsDict['id']
        if patientId >= max_tracks or patientId >= len(self.vitalsPatientData):
            return

        self.vitalsPatientData[patientId]['rangeBin'] = self.vitalsDict['rangeBin']
        self.vitalsPatientData[patientId]['breathDeviation'] = self.vitalsDict['breathDeviation']
        self.vitalsPatientData[patientId]['breathRate'] = self.vitalsDict['breathRate']

        self._dev_chip_raw = float(self.vitalsDict['breathDeviation'])
        deviation = self._smooth_deviation(self._dev_chip_raw)
        patientStatus, skip_wave = self._classify_breath(deviation, patientId)

        if not skip_wave:
            hr = float(self.vitalsDict['heartRate'])
            if hr > 0:
                self.vitalsPatientData[patientId]['heartRate'].append(hr)
                while len(self.vitalsPatientData[patientId]['heartRate']) > NUM_HEART_RATES_FOR_MEDIAN:
                    self.vitalsPatientData[patientId]['heartRate'].pop(0)
            rr = float(self.vitalsDict['breathRate'])
            if rr > 0:
                hist = self.vitalsPatientData[patientId].setdefault('breathRateHist', [])
                hist.append(rr)
                while len(hist) > NUM_HEART_RATES_FOR_MEDIAN:
                    hist.pop(0)
            self._append_waveforms(patientId)
            if patientStatus in ('Hold', 'Warning'):
                self.holdWavePtp = self._packet_ptp()

        heartRateList = self.vitalsPatientData[patientId]['heartRate'].copy()
        medianHeartRate = median(heartRateList)

        if medianHeartRate == 0:
            heartRateText = "Updating"
        elif self.xWRLx432 == 1:
            heartRateText = str(round(self.vitalsDict['heartWaveform'][0], 1))
        else:
            heartRateText = str(round(medianHeartRate, 1))

        if patientStatus in ('Hold', 'Warning'):
            breathRateText = "N/A"
        elif self.vitalsPatientData[patientId]['breathRate'] == 0:
            breathRateText = "Updating"
        else:
            breathRateText = str(round(self.vitalsPatientData[patientId]['breathRate'], 1))

        self.vitals[patientId]['heartRate'].setText(heartRateText)
        self.vitals[patientId]['breathRate'].setText(breathRateText)
        self._set_status(patientId, patientStatus)
        self.vitals[patientId]['rangeBin'].setText(str(self.vitalsPatientData[patientId]['rangeBin']))
        self._set_deviation(patientId, self._dev_chip_raw)

    def parseTrackingCfg(self, args):
        PeopleTracking.parseTrackingCfg(self, args)
        if (self.maxTracks == 1):
            self.vitals[1]['pane'].setVisible(False)
        self.vitalsPatientData = []
        for i in range(min(self.maxTracks,MAX_VITALS_PATIENTS)):
            patientDict = {}
            patientDict ['id'] = i
            patientDict ['rangeBin'] = 0
            patientDict ['breathDeviation'] = 0
            patientDict ['heartRate'] = []
            patientDict ['breathRate'] = 0
            patientDict ['breathRateHist'] = []
            patientDict ['heartWaveform'] = []
            patientDict ['breathWaveform'] = []
            self.vitalsPatientData.append(patientDict)
            self.vitals[i]['pane'].setVisible(True)
            self._set_status(i, '')
        self._reset_motion()
        self._reset_hold_arm()
        self.emptySince = None
        self._start_csv()
