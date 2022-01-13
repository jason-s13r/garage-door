import reolinkapi as api
from Fix import FixStreamAPIMixin

from secrets import CAMERA_USER, CAMERA_PASS, CAMERA_URL

class Fixed(api.Camera, FixStreamAPIMixin):
    def __init__(self, *args, **kwargs):
        super(Fixed, self).__init__(*args, **kwargs)


camera = Fixed(CAMERA_URL, CAMERA_USER, CAMERA_PASS)
camera.go_to_preset(index=3)
image = camera.get_snap2()
print(image)
