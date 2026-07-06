"""
실시간 메인: DCA1000 직접 제어 -> UDP 수신 -> point cloud -> 실시간 3D 표시.

사용 순서:
  1) mmWave Studio에서 lua Run (단, StartRecord/DCA config 부분 없이 레이더만 송출)
     -> 레이더가 chirp 무한 송출 (NumOfFrame=0)
  2) 이 스크립트 실행:  py realtime_main.py
     -> Python이 DCA에 스트리밍 시작 명령 -> 4098에서 받기 -> 실시간 표시

주의:
  - mmWave Studio가 4098/4096을 점유하면 충돌하므로, lua에서 DCA 관련
    (EthInit/Mode/PacketDelay/StartRecord)을 빼고 레이더 송출만 시켜야 함.
  - 만약 DCA 제어가 잘 안 되면(--no-dca-control), lua가 DCA까지 설정하게 두고
    이 스크립트는 수신만 하도록 실행: py realtime_main.py --no-dca-control
"""
import sys
import time
import numpy as np

from steaming import adcCapThread
import configuration as cfg
from pc_generation import (PointCloudProcessCFG, bin2np_frame,
                           frame2pointcloud, reg_data)

USE_DCA_CONTROL = '--no-dca-control' not in sys.argv
SHOW_PLOT = '--no-plot' not in sys.argv


def process_frame_to_pc(np_int16_frame, pcCFG, shift_arr):
    """steaming이 준 int16 프레임 -> point cloud (N,6)."""
    np_frame = bin2np_frame(np_int16_frame)
    pc = frame2pointcloud(np_frame, pcCFG)
    if pc.shape[0] == 0 or pc.shape[1] == 0:
        return np.zeros((128, 6), dtype=np.float32)
    raw = np.transpose(pc, (1, 0))
    raw[:, :3] = raw[:, :3] + shift_arr
    raw = reg_data(raw, 128)
    return raw


def main():
    pcCFG = PointCloudProcessCFG()
    shift_arr = cfg.MMWAVE_RADAR_LOC

    # 1) DCA1000에 스트리밍 시작 명령 (선택)
    if USE_DCA_CONTROL:
        try:
            from dca1000_control import DCA1000Control
            dca = DCA1000Control()
            dca.start_streaming()
            dca.close()  # config 포트는 닫음 (data 포트는 steaming이 따로 씀)
            time.sleep(0.5)
        except Exception as e:
            print('[warn] DCA control failed:', e)
            print('       -> lua가 DCA를 설정했다고 가정하고 수신만 진행')

    # 2) UDP 수신 스레드 시작 (포트 4098)
    cap = adcCapThread(1, 'adc')
    cap.start()
    print('[main] receiver started, waiting for packets on 4098...')

    # 3) 실시간 시각화 준비
    if SHOW_PLOT:
        import matplotlib
        import matplotlib.pyplot as plt
        from mpl_toolkits.mplot3d import Axes3D  # noqa
        plt.ion()
        fig = plt.figure(figsize=(8, 6))
        ax = fig.add_subplot(111, projection='3d')

    frame_count = 0
    try:
        while True:
            readItem, itemNum, lostFlag = cap.getFrame()
            if itemNum > 0:
                frame_count += 1
                pc = process_frame_to_pc(readItem, pcCFG, shift_arr)

                if SHOW_PLOT:
                    ax.cla()
                    ax.scatter(pc[:, 0], pc[:, 1], pc[:, 2],
                               c=pc[:, 3], cmap='jet', s=15)
                    ax.set_xlim(-3, 3)
                    ax.set_ylim(0, 6)
                    ax.set_zlim(-1, 3)
                    ax.set_xlabel('X (m)')
                    ax.set_ylabel('Y (m)')
                    ax.set_zlabel('Z (m)')
                    ax.set_title('Realtime PC - frame %d' % frame_count)
                    plt.pause(0.001)
                else:
                    if frame_count % 10 == 0:
                        print('frame %d, points=%d' % (frame_count, pc.shape[0]))

            elif itemNum == -1:
                # 버퍼 overwritten (처리가 수신을 못 따라감)
                print('[main]', readItem)
            elif itemNum == -2:
                # 아직 새 프레임 없음
                time.sleep(0.01)
    except KeyboardInterrupt:
        print('\n[main] stopping...')
    finally:
        cap.whileSign = False
        time.sleep(0.2)
        print('[main] done. total frames:', frame_count)


if __name__ == '__main__':
    main()
