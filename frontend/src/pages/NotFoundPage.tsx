import { Button } from "antd";
import { useNavigate } from "react-router-dom";

export default function NotFoundPage() {
  const navigate = useNavigate();
  return (
    <div className="page-container not-found-page">
      <div className="not-found-symbol" aria-hidden="true">404</div>
      <h1>Такой страницы нет</h1>
      <p>Возможно, ссылка устарела или в адресе есть опечатка.</p>
      <Button type="primary" size="large" onClick={() => navigate("/")}>На главную</Button>
    </div>
  );
}
