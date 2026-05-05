import { createRoot } from 'react-dom/client';
import dayjs from 'dayjs';
import 'dayjs/locale/zh-cn';
import App from './App.jsx';

dayjs.locale('zh-cn');

// 全局重置样式
document.body.style.margin = '0';
document.body.style.padding = '0';

createRoot(document.getElementById('root')).render(<App />);
