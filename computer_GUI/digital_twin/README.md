# Digital Twin

한 서버가 두 화면을 같이 제공합니다. 바이탈과 clearance는 `/event` 로 들어오지만
서로 상태를 덮어쓰지 않습니다.

```text
python digital_twin/server.py
```

- 비상 회피: [http://127.0.0.1:8766](http://127.0.0.1:8766)
- 시트 폴딩: [http://127.0.0.1:8766/cabin.html](http://127.0.0.1:8766/cabin.html)

`vital_signs/run_twin.py` 가 HR/RR/Warning을, `gtrack_clearance_demo` 가 좌석 점유를 넣습니다.
같은 COM에 두 펌웨어를 동시에 붙일 수는 없습니다.

```text
digital_twin/
  server.py     정적 파일 + /state + /event
  bridge.py     바이탈 POST
  index.html    비상 회피
  cabin.html    시트 폴딩
  js/twin.js
  js/cabin.js
  models/       캐빈 3D
```
