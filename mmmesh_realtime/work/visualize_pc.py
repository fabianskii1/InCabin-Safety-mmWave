"""
저장된 point cloud(npy)에서 특정 frame 하나를 3D 산점도로 시각화.

사용법:
    python visualize_pc.py pointcloud_output.npy [frame번호] [color]
    - frame번호 (기본 0)
    - color: v(velocity, 기본) | e(energy) | r(range)
"""
import sys
import numpy as np
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

npy_file = sys.argv[1] if len(sys.argv) > 1 else 'pointcloud_output.npy'
frame_idx = int(sys.argv[2]) if len(sys.argv) > 2 else 0
color_key = sys.argv[3] if len(sys.argv) > 3 else 'v'

data = np.load(npy_file)  # (frames, 128, 6): x,y,z,V,energy,R
print('loaded', npy_file, data.shape)

if frame_idx >= data.shape[0]:
    print('frame %d out of range (max %d)' % (frame_idx, data.shape[0] - 1))
    sys.exit(1)

pc = data[frame_idx]            # (128, 6)
x, y, z = pc[:, 0], pc[:, 1], pc[:, 2]

color_map = {'v': (3, 'Velocity (m/s)'), 'e': (4, 'Energy (dB)'), 'r': (5, 'Range (m)')}
cidx, clabel = color_map.get(color_key, color_map['v'])
c = pc[:, cidx]

fig = plt.figure(figsize=(8, 6))
ax = fig.add_subplot(111, projection='3d')
sc = ax.scatter(x, y, z, c=c, cmap='jet', s=20)
fig.colorbar(sc, label=clabel)

ax.set_xlabel('X (m)')
ax.set_ylabel('Y (m)')
ax.set_zlabel('Z (m)')
ax.set_title('Point Cloud - Frame %d' % frame_idx)
plt.tight_layout()
plt.show()
