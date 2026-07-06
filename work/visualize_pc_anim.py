"""
저장된 point cloud(npy)를 frame 순서대로 3D 애니메이션으로 재생.

사용법:
    python visualize_pc_anim.py pointcloud_output.npy [--save out.gif]

옵션:
    --save <파일명>  : 애니메이션을 gif로 저장 (pillow 필요)
"""
import sys
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

npy_file = sys.argv[1] if len(sys.argv) > 1 else 'pointcloud_output.npy'
save_path = None
if '--save' in sys.argv:
    i = sys.argv.index('--save')
    if i + 1 < len(sys.argv):
        save_path = sys.argv[i + 1]

data = np.load(npy_file)  # (frames, 128, 6)
print('loaded', npy_file, data.shape)
num_frames = data.shape[0]

fig = plt.figure(figsize=(8, 6))
ax = fig.add_subplot(111, projection='3d')

# 전체 범위로 축 고정 (frame마다 축이 흔들리지 않도록)
all_xyz = data[:, :, :3].reshape(-1, 3)
xmin, xmax = all_xyz[:, 0].min(), all_xyz[:, 0].max()
ymin, ymax = all_xyz[:, 1].min(), all_xyz[:, 1].max()
zmin, zmax = all_xyz[:, 2].min(), all_xyz[:, 2].max()


def update(f):
    ax.cla()
    pc = data[f]
    ax.scatter(pc[:, 0], pc[:, 1], pc[:, 2], c=pc[:, 3], cmap='jet', s=20)
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_zlim(zmin, zmax)
    ax.set_xlabel('X (m)')
    ax.set_ylabel('Y (m)')
    ax.set_zlabel('Z (m)')
    ax.set_title('Frame %d / %d' % (f, num_frames - 1))


ani = FuncAnimation(fig, update, frames=num_frames, interval=100)  # 100ms = 10FPS

if save_path:
    print('saving to', save_path, '...')
    ani.save(save_path, writer='pillow', fps=10)
    print('saved.')
else:
    plt.show()
