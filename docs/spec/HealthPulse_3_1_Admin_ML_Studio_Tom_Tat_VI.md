# HealthPulse 3.1 — Admin & AI Development Studio

**Mục tiêu:** biến HealthPulse thành một nền tảng vận hành từ dữ liệu thô tới mô phỏng, có thể điều hành hoàn toàn bằng web UI; các mô hình dự báo dịch và phương án phân bổ đều có thể kiểm chứng nguồn gốc.

**Track:** Climate-Resilient Worker Health. Phạm vi MVP: Brazil, sốt xuất huyết (mô hình chính), COVID-19 SARI và influenza SARI (tín hiệu hô hấp nặng khi dữ liệu đủ điều kiện), phân tích ngành nghề, nguồn lực khám chữa bệnh và mô phỏng nhu cầu.

## 1. Bốn nhóm chức năng Admin

| Nhóm | Giao diện và tác vụ |
|---|---|
| **Data Source Manager** | Quản lý 8 nhóm nguồn; thông tin/điều kiện sử dụng; cấu hình schedule; test API; import dữ liệu; duyệt schema; xem lịch sử snapshot, DQ và lỗi; backfill; quarantine. |
| **Feature Store Studio** | Danh mục feature theo nguồn, bệnh, đơn vị, độ trễ; trình tạo feature từ template; kiểm tra point-in-time và duplicate join; xây dựng feature set có version; tạo training dataset bất biến. |
| **AI Model Development** | Wizard chọn target, feature, thuật toán, horizon, temporal split; chạy background training, tune trong giới hạn; xem fold metrics, ablation, calibration, model card và lineage. |
| **Model Registry & Deployment** | Quản lý model version, phê duyệt chuyên môn, shadow run, promote champion, batch inference, theo dõi sai số/drift và rollback. |

## 2. Trải nghiệm end-to-end

1. Người vận hành kích hoạt InfoDengue, kiểm tra thời gian cập nhật và import bản phát hành mới.
2. Pipeline lưu Bronze bất biến, chuẩn hóa Silver, công bố báo cáo chất lượng và thời điểm dữ liệu sẵn có (`available_at`).
3. Data Scientist tạo feature `cases_lag_2w`, `rain_sum_4w`, `holiday_ahead_7d`, kiểm tra nguồn gốc và độ trễ công bố.
4. Dataset Builder đóng băng dữ liệu theo từng thời điểm dự báo; **không sử dụng những bản sửa đổi xuất hiện sau thời điểm dự báo**.
5. Model Studio chạy seasonal naive và HGB Poisson, hiển thị metrics theo từng fold, khu vực và horizon, cùng tỷ lệ bao phủ khoảng dự báo.
6. Reviewer phê duyệt model card và deployment; model mới chạy shadow trước, sau đó mới trở thành champion.
7. Web Overview sử dụng forecast đã công bố; Simulation Studio pin `forecast_id` + assumptions + source vintage; người dùng chạy lại vẫn nhận được kết quả tương ứng phiên bản đó.

## 3. Stack khuyến nghị

- **Data processing:** Polars LazyFrame, Parquet, DuckDB (tùy chọn cho truy vấn phân tích).
- **Metadata / multiuser:** PostgreSQL; SQLite chỉ cho demo một máy.
- **API:** FastAPI + Pydantic v2, RBAC phía server, OpenAPI typed client.
- **Jobs:** queue có persistence (Redis/RQ hoặc Arq/Celery), worker chuyên biệt cho ingestion/training, giới hạn tài nguyên và timeout.
- **ML experiments/registry:** MLflow với database-backed backend và artifact store bền vững.
- **Frontend:** React/TypeScript, UI form/wizard, bảng tìm kiếm, biểu đồ tiến trình thực tế, trạng thái kiểm tra dữ liệu.
- **Optional P2:** Feast và online feature store, chỉ khi có nhu cầu độ trễ thấp đã đo được.

## 4. Ưu tiên triển khai

**P0 trước hackathon:** một source thật + source status UI → feature set PIT-safe → training dataset → model training + metrics thật → registry/review đơn giản → forecast versioned → simulation sử dụng đúng version. Giữ bản đồ và web dự báo thật là yêu cầu nền tảng.

**P1 sau MVP:** đa vai trò nâng cao, monitor drift theo khu vực, điều phối retrain, thêm nhiều nguồn và hiển thị lineage phong phú.

**P2:** Feast/online serving, AutoML lớn, worker phân tán, mô phỏng lây truyền chuyên sâu có hiệu chỉnh.

## 5. Tiêu chuẩn nghiệm thu

- Không có dữ liệu ca mắc, mobility, occupancy giả được biểu diễn như dữ liệu thật.
- Tất cả tác vụ chạy từ UI phải có API, job_id và trạng thái thực; train không chạy bên trong request FastAPI.
- Mọi experiment gắn với data snapshot, feature set, source vintage và split bất biến.
- Leak test phải chặn feature có `available_at` sau ngày dự báo; join không được nhân bản số ca.
- Reviewer/admin mới được approve/promote/rollback; mọi thay đổi có audit log.
- Model và mô phỏng đã công bố không thay đổi khi dữ liệu nguồn hoặc alias model được cập nhật.
- SIVEP được tổng hợp/ẩn danh; không lộ bệnh nhân, worker hoặc secret trong API và admin log.

## 6. Tài liệu kỹ thuật trong bộ ZIP

- `docs/IMPLEMENTATION_PLAN_AI_SIMULATION_WEB.md` — kế hoạch tổng thể 3.1 (đã gộp với kế hoạch 3.0)
- `docs/ADMIN_ML_STUDIO_IMPLEMENTATION.md` — thiết kế Admin & Feature Store & Model Studio chuyên sâu
- `docs/ADMIN_API_CONTRACT_EXAMPLES.md` — request/response cho ingest, feature, training, release
- `docs/ADMIN_SCREEN_ACCEPTANCE.md` — tiêu chí nghiệm thu theo hành trình UI
- `config/admin/*.example.yaml` — cấu hình nguồn, feature và experiment mẫu

**Trạng thái:** bộ tài liệu là specification để triển khai. Repo đi kèm là starter Polars hiện có, chưa có chức năng Admin web mới, hàng đợi job hay MLflow server đang chạy.
