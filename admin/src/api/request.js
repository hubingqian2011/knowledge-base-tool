import axios from 'axios';
import { message } from 'antd';

const request = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  timeout: 30000,
});

// 请求拦截：注入 token
request.interceptors.request.use((config) => {
  const token = localStorage.getItem('admin_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// 响应拦截：统一错误处理
request.interceptors.response.use(
  (response) => response.data,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('admin_token');
      localStorage.removeItem('admin_user');
      window.location.hash = '#/login';
      message.error('登录已过期，请重新登录');
    } else if (error.response?.status === 403) {
      message.error('权限不足');
    } else {
      const msg = error.response?.data?.detail || error.response?.data?.msg || error.message;
      message.error(msg || '请求失败');
    }
    return Promise.reject(error);
  },
);

export default request;
