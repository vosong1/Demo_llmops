/* LLMOps Workbench: Vietnamese / English UI.
 * Only translates explicitly selected UI elements.
 * Does not modify API responses, chat content, documents or activity logs.
 */
(() => {
  "use strict";

  if (window.LLMOpsI18n) return;

  const STORAGE_KEY = "llmops.ui.language";

  let language = "vi";
  try {
    if (localStorage.getItem(STORAGE_KEY) === "en") language = "en";
  } catch (_) {}

  // Vietnamese source text -> English UI text.
  const TEXT = new Map(Object.entries({
    // Authentication
    "Đăng nhập · LLMOps Workbench": "Sign in · LLMOps Workbench",
    "Tên đăng nhập": "Username",
    "Mật khẩu": "Password",
    "Nhập lại mật khẩu": "Confirm password",
    "Hiện mật khẩu": "Show password",
    "Đăng nhập": "Sign in",
    "Đăng xuất": "Sign out",
    "Đăng nhập để tiếp tục.": "Sign in to continue.",
    "Chưa có tài khoản nào. Tạo tài khoản quản trị đầu tiên để bắt đầu.":
      "No accounts yet. Create the first administrator account to get started.",
    "Tạo tài khoản quản trị": "Create administrator account",
    "Tài khoản này có toàn quyền. Hãy nhớ mật khẩu. Nếu quên, chạy: python dashboard.py --set-password TÊN":
      "This account has full access. Keep your password safe. To recover access, run: python dashboard.py --set-password USERNAME",
    "Đang xử lý…": "Processing…",
    "Không đăng nhập được.": "Unable to sign in.",
    "Không kết nối được máy chủ.": "Unable to connect to the server.",
    "Mật khẩu cần tối thiểu 8 ký tự.": "Password must contain at least 8 characters.",
    "Mật khẩu tối đa 128 ký tự.": "Password must not exceed 128 characters.",
    "Hai mật khẩu không khớp.": "Passwords do not match.",
    "Hai mật khẩu mới không khớp.": "New passwords do not match.",
    "Sai tên đăng nhập hoặc mật khẩu.": "Incorrect username or password.",
    "Tên đăng nhập gồm 3-32 ký tự: chữ, số, dấu . _ -":
      "Username must contain 3–32 characters: letters, numbers, . _ -",
    "Tên đăng nhập đã tồn tại.": "Username already exists.",
    "Đã có tài khoản. Hãy đăng nhập.": "An account already exists. Please sign in.",

    // Navigation / roles
    "Mã bản đang chạy": "Current build",
    "Tổng quan": "Overview",
    "Hỏi đáp": "Chat",
    "Sử dụng": "Usage",
    "Hoạt động": "Activity",
    "Cấu hình": "Configuration",
    "Người dùng": "User",
    "Tài khoản": "Account",
    "Quản trị": "Admin",
    "Quản trị viên": "Administrator",

    // Overview
    "Lượt hỏi hôm nay": "Requests today",
    "Độ trễ p50 / p95 (giây)": "p50 / p95 latency (seconds)",
    "các lượt đã gọi model": "requests that called a model",
    "Bị chặn bởi guardrail / hạn mức": "Blocked by guardrails / limits",
    "Người dùng đánh giá": "User feedback",
    "Chưa có lượt hỏi nào": "No requests yet",
    "Chưa có đánh giá": "No feedback yet",
    "Dịch vụ": "Services",
    "VRAM đang dùng": "VRAM usage",
    "Đang đọc GPU…": "Reading GPU…",
    "Đang chờ dữ liệu từ NVIDIA": "Waiting for NVIDIA data",
    "Phần trăm tải GPU": "GPU load percentage",
    "Nhiệt độ": "Temperature",
    "GPU chưa xác định": "Unknown GPU",
    "3 phút gần nhất, chỉ ghi khi tab Tổng quan đang mở":
      "Last 3 minutes; recorded only while Overview is open",
    "Tự cập nhật mỗi 2 giây bằng nvidia-smi. VRAM phản ánh toàn GPU, không chỉ riêng Ollama.":
      "Updated every 2 seconds using nvidia-smi. VRAM usage covers the entire GPU, not just Ollama.",
    "Không khả dụng": "Unavailable",
    "Live · cập nhật 2s": "Live · updated every 2s",
    "Mất kết nối": "Disconnected",
    "Cần NVIDIA driver và nvidia-smi": "NVIDIA drivers and nvidia-smi are required",
    "Không tìm thấy nvidia-smi. Cần GPU NVIDIA và driver đã cài; nếu dùng CPU-only thì mục này không có dữ liệu.":
      "nvidia-smi was not found. An NVIDIA GPU and installed drivers are required. This panel has no data in CPU-only mode.",
    "Model Ollama": "Ollama models",
    "Đang đọc…": "Loading…",
    "Đang nạp trong bộ nhớ": "Currently loaded in memory",
    "Đã tải về máy": "Downloaded models",
    "Máy chủ": "Host system",
    "Model lớn hơn VRAM sẽ dùng thêm RAM và chạy chậm hơn.":
      "Models exceeding VRAM capacity will also use system RAM and run more slowly.",
    "Đang chạy": "Running",
    "Chưa chạy": "Not running",
    "Bật Ollama để xem model.": "Start Ollama to view models.",
    "Chưa nạp model nào (sẽ nạp khi có câu hỏi đầu tiên).":
      "No models loaded yet. A model will load on the first request.",
    "Chưa tải model nào. Dùng: ollama pull qwen2.5:3b":
      "No models downloaded. Run: ollama pull qwen2.5:3b",

    // Services
    "Bật": "Start",
    "Tắt": "Stop",
    "Khởi động lại": "Restart",
    "Tắt rồi bật lại, dùng khi vừa đổi cấu hình":
      "Stop and start again to apply configuration changes",
    "Đang bật…": "Starting…",
    "Đang tắt…": "Stopping…",
    "Đang khởi động lại…": "Restarting…",
    "chạy model 3B, 7B": "serves 3B and 7B models",
    "gateway: key, giới hạn, fallback": "gateway: keys, limits, fallback",
    "xem trace tại localhost:6006": "view traces at localhost:6006",
    "Đã chạy sẵn.": "Already running.",
    "Đang khởi động, chờ vài giây.": "Starting; please wait a few seconds.",
    "Đã dừng.": "Stopped.",
    "Dịch vụ đang tắt.": "The service is not running.",
    "Đã hủy.": "Cancelled.",
    "Không kết nối được bảng điều khiển.": "Unable to connect to the dashboard.",
    "Đã gửi lệnh tắt nhưng cổng vẫn mở.": "A stop command was sent, but the port is still open.",
    "Đã gửi lệnh tắt nhưng cổng vẫn mở (Ollama có thể đã tự khởi động lại từ khay hệ thống).":
      "A stop command was sent, but the port is still open. Ollama may have restarted from the system tray.",
    "Ollama chạy từ khay hệ thống có thể tự bật lại; muốn tắt hẳn hãy chọn Quit ở khay.":
      "Ollama may restart automatically from the system tray. Select Quit in the tray to stop it completely.",

    // Activity / usage: labels only, not log records
    "Hoạt động trực tiếp": "Live activity",
    "Đang kết nối...": "Connecting...",
    "Tải log (CSV)": "Download log (CSV)",
    "Giờ": "Time",
    "Ai": "User",
    "Làm gì": "Action",
    "Chi tiết": "Details",
    "Kết quả": "Result",
    "Mất kết nối bảng điều khiển": "Dashboard connection lost",
    "Token và chi phí hôm nay": "Today's tokens and cost",
    "Lượt": "Requests",
    "Token vào": "Input tokens",
    "Token ra": "Output tokens",
    "Chi phí (USD)": "Cost (USD)",
    "Hạn mức chỉnh trong limits.json, giá model trong models.json (USD cho 1 triệu token).":
      "Limits are configured in limits.json; model prices in models.json (USD per million tokens).",
    "Chưa có lượt hỏi nào hôm nay.": "No requests today.",
    "7 ngày gần đây": "Last 7 days",
    "Chiều cao cột là số lượt hỏi mỗi ngày. Rê chuột lên cột để xem số token.":
      "Bar height shows daily request counts. Hover over a bar to view token usage.",

    // Chat
    "Ví dụ: Nhân viên chính thức được nghỉ phép mấy ngày?":
      "Example: How many days of leave do full-time employees get?",
    "Hỏi": "Ask",
    "Đang hỏi...": "Asking...",
    "1. Kiểm tra": "1. Validate",
    "2. Tìm tài liệu": "2. Retrieve",
    "3. Gọi LLM": "3. Call LLM",
    "4. Hoàn tất": "4. Complete",
    "Đang kiểm tra yêu cầu": "Validating request",
    "Đang truy xuất tài liệu": "Retrieving documents",
    "AI đang suy luận và tạo câu trả lời": "AI is generating a response",
    "Hoàn tất": "Complete",
    "Sẵn sàng": "Ready",
    "Đang kiểm tra câu hỏi và hạn mức sử dụng…": "Checking the question and usage limits…",
    "Đang tìm các đoạn tài liệu liên quan trong Knowledge Base…":
      "Searching the Knowledge Base for relevant passages…",
    "Đã hoàn tất câu trả lời.": "Response completed.",
    "Thêm tài liệu vào Knowledge Base": "Add document to Knowledge Base",
    "Làm mới kho tài liệu": "Refresh Knowledge Base",
    "Tài liệu được chia chunk và truy xuất theo mức độ liên quan trước khi đưa vào prompt. Chỉ nhận .txt/.md.":
      "Documents are chunked and retrieved by relevance before being added to the prompt. Only .txt/.md files are supported.",
    "Lịch sử chat của tôi": "My chat history",
    "Xóa lịch sử": "Clear history",
    "Xem thêm": "Load more",
    "Chưa có lịch sử": "No history yet",
    "Knowledge Base đang trống. Thêm file .txt/.md để bật RAG có nguồn.":
      "The Knowledge Base is empty. Add .txt/.md files to enable source-grounded RAG.",
    "Knowledge Base đang trống. Hãy nhờ quản trị viên thêm tài liệu.":
      "The Knowledge Base is empty. Ask an administrator to add documents.",
    "Đúng": "Correct",
    "Sai": "Incorrect",
    "Bạn đánh giá: đúng": "Your rating: correct",
    "Bạn đánh giá: sai": "Your rating: incorrect",
    "không gọi model": "no model call",
    "Ollama trực tiếp": "Direct Ollama",
    "Ollama trực tiếp (LiteLLM lỗi)": "Direct Ollama (LiteLLM failed)",
    "Xóa toàn bộ lịch sử chat của bạn? Không thể khôi phục.":
      "Delete all your chat history? This cannot be undone.",
    "Chọn file .txt hoặc .md trước.": "Select a .txt or .md file first.",
    "Chỉ hỗ trợ .txt và .md": "Only .txt and .md files are supported",
    "Xóa tài liệu": "Delete document",
    "Đã xóa lịch sử chat của bạn.": "Your chat history has been cleared.",
    "Hãy nhập câu hỏi.": "Please enter a question.",
    "Model không hợp lệ.": "Invalid model.",
    "Tài khoản của bạn chưa được phép dùng model đám mây. Hãy nhờ quản trị viên cấp quyền.":
      "Your account is not allowed to use cloud models. Ask an administrator for access.",
    "Model đám mây cần LiteLLM đang chạy (khóa API nằm ở LiteLLM). Bấm Bật LiteLLM.":
      "Cloud models require LiteLLM to be running; API keys are managed there. Start LiteLLM.",
    "Ollama chưa chạy. Bấm Bật ở dòng Ollama.":
      "Ollama is not running. Click Start next to Ollama.",
    "câu hỏi quá dài": "question is too long",
    "yêu cầu bỏ qua quy tắc": "request to ignore rules",
    "hỏi về chỉ dẫn hệ thống": "request for system instructions",
    "chứa dãy 12 số giống số giấy tờ": "contains a 12-digit sequence resembling an identification number",
    "hết ngân sách đám mây hôm nay": "today's cloud budget has been exhausted",
    "lộ chỉ dẫn hệ thống": "possible system instruction disclosure",

    // Configuration
    "Hạn mức sử dụng": "Usage limits",
    "Lưu là áp dụng ngay": "Changes apply immediately after saving",
    "Token mỗi người mỗi ngày (0 = không giới hạn)":
      "Tokens per user per day (0 = unlimited)",
    "Ngân sách model đám mây mỗi ngày (USD, 0 = không giới hạn)":
      "Daily cloud model budget (USD, 0 = unlimited)",
    "Hạn mức riêng từng người (mỗi dòng một người, dạng: tên=số token)":
      "Per-user limits (one per line: username=token limit)",
    "Lưu hạn mức": "Save limits",
    "Giá tính theo USD cho 1 triệu token": "Prices in USD per million tokens",
    "Tên": "Name",
    "Loại": "Type",
    "Giá vào": "Input price",
    "Giá ra": "Output price",
    "Thêm hoặc sửa model": "Add or edit model",
    "Tên model": "Model name",
    "Alias trong LiteLLM": "LiteLLM alias",
    "Model đám mây (gọi qua LiteLLM)": "Cloud model (via LiteLLM)",
    "Lưu model": "Save model",
    "Model đám mây còn cần khối cấu hình tương ứng trong litellm_config.yaml và khóa API trong biến môi trường. Xem README mục G.":
      "Cloud models also require matching configuration in litellm_config.yaml and API keys in environment variables. See README section G.",
    "đám mây": "cloud",
    "Sửa": "Edit",
    "Xóa": "Delete",
    "Dữ liệu hạn mức không hợp lệ.": "Invalid usage limit data.",
    "Hạn mức không được âm.": "Limits cannot be negative.",
    "Đã lưu hạn mức, áp dụng ngay.": "Limits saved and applied.",
    "Tên model và alias chỉ gồm chữ, số, dấu . : - _ (tối đa 60 ký tự).":
      "Model names and aliases may contain letters, numbers, . : - _ (up to 60 characters).",
    "Giá không hợp lệ.": "Invalid price.",
    "Giá không được âm.": "Prices cannot be negative.",
    "Không tìm thấy model.": "Model not found.",
    "Phải còn ít nhất một model.": "At least one model must remain.",

    // Users / account
    "Vai trò": "Role",
    "Trạng thái": "Status",
    "Model đám mây": "Cloud models",
    "Đăng nhập gần nhất": "Last sign-in",
    "Lịch sử chat là riêng của từng người. Quản trị viên không đọc được nội dung chat của người khác, chỉ thấy số liệu sử dụng.":
      "Chat history is private to each user. In this dashboard, administrators can view usage statistics, not other users' chat content.",
    "Thêm người dùng": "Add user",
    "Mật khẩu tạm (tối thiểu 8 ký tự)": "Temporary password (at least 8 characters)",
    "Cho phép dùng model đám mây": "Allow cloud models",
    "Tạo tài khoản": "Create account",
    "Đặt lại mật khẩu": "Reset password",
    "Người đó sẽ bị đăng xuất": "The user will be signed out",
    "Mật khẩu mới": "New password",
    "Tài khoản của tôi": "My account",
    "Đổi mật khẩu": "Change password",
    "Mật khẩu hiện tại": "Current password",
    "Mật khẩu mới (tối thiểu 8 ký tự)": "New password (at least 8 characters)",
    "Nhập lại mật khẩu mới": "Confirm new password",
    "Đổi mật khẩu sẽ đăng xuất các thiết bị khác của bạn.":
      "Changing your password will sign out your other devices.",
    "Được phép": "Allowed",
    "Không được phép": "Not allowed",
    "Không": "No",
    "(bạn)": "(you)",
    "Đã khóa": "Locked",
    "Hạ xuống người dùng": "Demote to user",
    "Nâng lên quản trị": "Promote to administrator",
    "Mở khóa": "Unlock",
    "Khóa": "Lock",
    "Cấm đám mây": "Disable cloud access",
    "Cho phép đám mây": "Allow cloud access",
    "Vai trò không hợp lệ.": "Invalid role.",
    "Không tìm thấy người dùng.": "User not found.",
    "Không thể tự đổi vai trò hoặc tự khóa chính mình.":
      "You cannot change your own role or lock your own account.",
    "Phải còn ít nhất một quản trị viên đang hoạt động.":
      "At least one active administrator must remain.",
    "Không thể tự xóa chính mình.": "You cannot delete your own account.",
    "Đã cập nhật tài khoản.": "Account updated.",
    "Hãy dùng mục Tài khoản để đổi mật khẩu của chính bạn.":
      "Use the Account tab to change your own password.",
    "Đã đặt lại mật khẩu; người này bị đăng xuất.":
      "Password reset. The user has been signed out.",
    "Mật khẩu hiện tại không đúng.": "The current password is incorrect.",
    "Đã đổi mật khẩu. Các thiết bị khác đã bị đăng xuất.":
      "Password changed. Your other devices have been signed out.",

    // API errors shown by the UI
    "Cần quyền quản trị.": "Administrator access is required.",
    "Phiên không hợp lệ. Hãy tải lại trang.": "Invalid session. Please reload the page.",
    "Dữ liệu quá lớn.": "The submitted data is too large.",
    "Không tìm thấy câu hỏi.": "Question not found.",
    "Tên file không hợp lệ; chỉ nhận .txt/.md.":
      "Invalid filename; only .txt/.md files are accepted.",
    "Tài liệu trống hoặc không đọc được.": "The document is empty or unreadable.",
    "File quá lớn (giới hạn 2 MB).": "File is too large (2 MB limit).",
    "Không tìm thấy tài liệu.": "Document not found.",
    "unauthorized": "Please sign in.",
    "forbidden": "Access denied.",
    "not found": "Not found.",
    "bad request": "Invalid request."
  }));

  // Anchored patterns: preserve captured names, IDs, paths and error details.
  const RULES = [
    [/^(\d+) đã trả lời · (\d+) người dùng$/, (_, n, u) =>
      `${