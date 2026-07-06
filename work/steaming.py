import socket
import struct
import threading
import time
import array as arr
import numpy as np

ADC_PARAMS = {'chirps': 128,
              'rx': 4,
              'tx': 3,
              'samples': 256,
              'IQ': 2,
              'bytes': 2}
# STATIC
MAX_PACKET_SIZE = 4096
BYTES_IN_PACKET = 1456

# DYNAMIC
BYTES_IN_FRAME = (ADC_PARAMS['chirps'] * ADC_PARAMS['rx'] * ADC_PARAMS['tx'] *
                  ADC_PARAMS['IQ'] * ADC_PARAMS['samples'] * ADC_PARAMS['bytes'])
BYTES_IN_FRAME_CLIPPED = (BYTES_IN_FRAME // BYTES_IN_PACKET) * BYTES_IN_PACKET
PACKETS_IN_FRAME = BYTES_IN_FRAME / BYTES_IN_PACKET
PACKETS_IN_FRAME_CLIPPED = BYTES_IN_FRAME // BYTES_IN_PACKET
UINT16_IN_PACKET = BYTES_IN_PACKET // 2
UINT16_IN_FRAME = BYTES_IN_FRAME // 2

class adcCapThread (threading.Thread):
    def __init__(self, threadID, name, static_ip='192.168.33.30', adc_ip='192.168.33.180',
                 data_port=4098, config_port=4096, bufferSize = 1500):
        threading.Thread.__init__(self)
        self.whileSign = True
        self.threadID = threadID
        self.name = name
        self.recentCapNum = 0
        self.lostFrameCount = 0
        self.latestReadNum = 0
        self.nextReadBufferPosition = 0
        self.nextCapBufferPosition = 0
        self.bufferOverWritten = True
        self.bufferSize = bufferSize
    
        # Create configuration and data destinations
        self.cfg_dest = (adc_ip, config_port)
        self.cfg_recv = (static_ip, config_port)
        self.data_recv = (static_ip, data_port)

        # Create sockets
        self.config_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.data_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)

        # Bind data socket to fpga
        self.data_socket.bind(self.data_recv)
        self.data_socket.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,2**27)

        # Bind config socket to fpga
        self.config_socket.bind(self.cfg_recv)

        self.bufferArray = np.zeros((self.bufferSize,BYTES_IN_FRAME//2), dtype = np.int16)
        self.itemNumArray = np.zeros(self.bufferSize, dtype = np.int32)
        self.lostPackeFlagtArray = np.zeros(self.bufferSize,  dtype = bool)

    def run(self):
        self._frame_receiver()
    
    def _frame_receiver(self):
        # 프레임 경계에 맞춰 패킷을 조립한다. 패킷 유실이 생겨도 스레드를 종료하지 않고,
        # 조립 중이던(구멍 난) 프레임만 폐기한 뒤 다음 프레임 경계부터 재동기화한다.
        # byte_count는 DCA 하드웨어의 절대 누적 바이트라 유실에도 어긋나지 않으므로,
        # 시작 시점의 경계 탐색 로직을 재활용해 언제든 다시 정렬할 수 있다.
        self.data_socket.settimeout(1)
        recentframe = np.zeros(UINT16_IN_FRAME, dtype=np.int16)
        recentframe_collect_count = 0
        last_packet_num = -1
        need_resync = True  # 시작 시점 및 유실 직후엔 프레임 경계부터 다시 맞춘다

        while self.whileSign:
            try:
                packet_num, byte_count, packet_data = self._read_data_packet()
            except socket.timeout:
                # 데이터가 잠깐 끊겨도 스레드를 죽이지 않고 계속 대기
                continue

            # ---- 재동기화: 이 패킷 안에서 프레임 경계를 찾는다 ----
            if need_resync:
                after_packet_count = (byte_count + BYTES_IN_PACKET) % BYTES_IN_FRAME
                if after_packet_count < BYTES_IN_PACKET:
                    recentframe = np.zeros(UINT16_IN_FRAME, dtype=np.int16)
                    recentframe[0:after_packet_count // 2] = packet_data[(BYTES_IN_PACKET - after_packet_count) // 2:]
                    self.recentCapNum = (byte_count + BYTES_IN_PACKET) // BYTES_IN_FRAME
                    recentframe_collect_count = after_packet_count
                    need_resync = False
                last_packet_num = packet_num
                continue

            # ---- 패킷 유실 감지: 종료하지 않고 현재 프레임 폐기 후 재동기화 ----
            if last_packet_num < packet_num - 1:
                self.lostFrameCount += 1
                print("Packet Lost! 현재 프레임 폐기 후 재동기화 (누적 유실: %d)" % self.lostFrameCount)
                need_resync = True
                last_packet_num = packet_num
                # 이 패킷으로 곧바로 재정렬 시도(경계가 이 패킷 안에 있으면 바로 복구)
                after_packet_count = (byte_count + BYTES_IN_PACKET) % BYTES_IN_FRAME
                if after_packet_count < BYTES_IN_PACKET:
                    recentframe = np.zeros(UINT16_IN_FRAME, dtype=np.int16)
                    recentframe[0:after_packet_count // 2] = packet_data[(BYTES_IN_PACKET - after_packet_count) // 2:]
                    self.recentCapNum = (byte_count + BYTES_IN_PACKET) // BYTES_IN_FRAME
                    recentframe_collect_count = after_packet_count
                    need_resync = False
                continue

            # ---- 정상 조립 ----
            # 이 패킷에서 프레임이 완성되는 경우
            if recentframe_collect_count + BYTES_IN_PACKET >= BYTES_IN_FRAME:
                recentframe[recentframe_collect_count // 2:] = packet_data[:(BYTES_IN_FRAME - recentframe_collect_count) // 2]
                self._store_frame(recentframe)
                self.recentCapNum = (byte_count + BYTES_IN_PACKET) // BYTES_IN_FRAME
                recentframe = np.zeros(UINT16_IN_FRAME, dtype=np.int16)
                after_packet_count = (recentframe_collect_count + BYTES_IN_PACKET) % BYTES_IN_FRAME
                recentframe[0:after_packet_count // 2] = packet_data[(BYTES_IN_PACKET - after_packet_count) // 2:]
                recentframe_collect_count = after_packet_count
            else:
                after_packet_count = (recentframe_collect_count + BYTES_IN_PACKET) % BYTES_IN_FRAME
                recentframe[recentframe_collect_count // 2:after_packet_count // 2] = packet_data
                recentframe_collect_count = after_packet_count
            last_packet_num = packet_num
    
    def getFrame(self):
        if self.latestReadNum != 0:
            if self.bufferOverWritten == True:
                return "bufferOverWritten",-1,False
        else: 
            self.bufferOverWritten = False
        nextReadPosition = (self.nextReadBufferPosition+1)%self.bufferSize 
        if self.nextReadBufferPosition == self.nextCapBufferPosition:
            return "wait new frame",-2,False
        else:
            readframe = self.bufferArray[self.nextReadBufferPosition]
            self.latestReadNum = self.itemNumArray[self.nextReadBufferPosition]            
            lostPacketFlag = self.lostPackeFlagtArray[self.nextReadBufferPosition]
            self.nextReadBufferPosition = nextReadPosition
        return readframe,self.latestReadNum,lostPacketFlag
    
    def _store_frame(self,recentframe):
        self.bufferArray[self.nextCapBufferPosition] = recentframe                    
        self.itemNumArray[self.nextCapBufferPosition] = self.recentCapNum
        if((self.nextReadBufferPosition-1+self.bufferSize)%self.bufferSize == self.nextCapBufferPosition):
            self.bufferOverWritten = True
        self.nextCapBufferPosition += 1
        self.nextCapBufferPosition %= self.bufferSize

    def _read_data_packet(self):
        """
        Returns:
            int: Current packet number, byte count of data that has already been read, raw ADC data in current packet
        """
        data, addr = self.data_socket.recvfrom(MAX_PACKET_SIZE)
        packet_num = struct.unpack('<1l', data[:4])[0]

        byte_count = struct.unpack('>Q', b'\x00\x00' + data[4:10][::-1])[0]
        packet_data = np.frombuffer(data[10:], dtype=np.uint16)
        return packet_num, byte_count, packet_data

