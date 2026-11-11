# 개인 사진 보관함

원본 개인 사진은 이 폴더에만 저장한다. 이 폴더의 파일은 Git에 올리지 않으며 외부 AI에 전달하기 전에 운영자가 사진별 사용 범위와 개인정보 노출 여부를 확인한다.

사진마다 별도 메타데이터 파일을 만들고 아래 항목을 기록한다.

```yaml
asset_id: personal-YYYYMMDD-01
owner_confirmed: true
publish_permission: false
contains_face: false
contains_location_or_document: false
caption_facts: []
```
