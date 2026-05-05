import { useState, useEffect } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Layout, Menu, Avatar, Typography, Tag, Space, Popconfirm } from 'antd';
import {
  BarChartOutlined,
  BookOutlined,
  MessageOutlined,
  TeamOutlined,
  LogoutOutlined,
} from '@ant-design/icons';
import { getUser, getUserRole, canAccessAdminPath, getDefaultAdminPath, logout } from '../utils/auth';

const { Sider, Header, Content } = Layout;
const { Text } = Typography;

const ALL_MENU_ITEMS = [
  { key: '/dashboard', icon: <BarChartOutlined />, label: '数据概览' },
  { key: '/knowledge', icon: <BookOutlined />, label: '知识库管理' },
  { key: '/chat', icon: <MessageOutlined />, label: '对话管理' },
  { key: '/users', icon: <TeamOutlined />, label: '用户管理' },
];

const pageTitles = {
  '/dashboard': '数据概览',
  '/knowledge': '知识库管理',
  '/chat': '对话管理',
  '/users': '用户管理',
};

export default function AdminLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const user = getUser();
  const role = getUserRole(user);
  const [time, setTime] = useState('');

  useEffect(() => {
    const tick = () => {
      setTime(new Date().toLocaleString('zh-CN', { hour12: false }));
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  const currentPath = '/' + (location.pathname.split('/')[1] || 'dashboard');

  useEffect(() => {
    if (!canAccessAdminPath(currentPath, user)) {
      navigate(getDefaultAdminPath(user), { replace: true });
    }
  }, [currentPath, navigate, user]);

  const menuItems = ALL_MENU_ITEMS.filter((item) => canAccessAdminPath(item.key, user));
  const pageTitle = pageTitles[currentPath] || 'Knowledge Base Tool';
  const roleLabel = role === 'reviewer' ? '审核员' : '管理员';

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        width={240}
        style={{
          background: 'linear-gradient(185deg, #0f1d32 0%, #1e3a5f 100%)',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <div style={{
          padding: '20px 24px',
          borderBottom: '1px solid rgba(255,255,255,.08)',
          display: 'flex',
          alignItems: 'center',
          gap: 12,
        }}>
          <div style={{
            width: 36,
            height: 36,
            background: '#3b82f6',
            borderRadius: 10,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontWeight: 700,
            fontSize: 16,
            color: '#fff',
            boxShadow: '0 2px 8px rgba(59,130,246,.4)',
          }}>X</div>
          <div>
            <div style={{ color: '#fff', fontSize: 16, fontWeight: 600, letterSpacing: 0.5 }}>
              Knowledge Base Tool
            </div>
            <div style={{ color: 'rgba(255,255,255,.5)', fontSize: 11, marginTop: 2 }}>
              Admin Console
            </div>
          </div>
        </div>

        <div style={{ padding: '16px 12px 6px', fontSize: 11, color: 'rgba(255,255,255,.35)', letterSpacing: 1, fontWeight: 600 }}>
          主菜单
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[currentPath]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
          style={{ background: 'transparent', borderRight: 'none' }}
        />

        <div style={{
          marginTop: 'auto',
          padding: '16px 20px',
          borderTop: '1px solid rgba(255,255,255,.08)',
          display: 'flex',
          alignItems: 'center',
          gap: 10,
        }}>
          <Avatar
            size={32}
            style={{ background: '#2563eb', fontSize: 13, fontWeight: 600 }}
          >
            {(user?.username || '管')[0]}
          </Avatar>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ color: 'rgba(255,255,255,.85)', fontSize: 13 }}>
              {user?.full_name || user?.username || '管理员'}
            </div>
            <div style={{ color: 'rgba(255,255,255,.45)', fontSize: 11 }}>
              {roleLabel}
            </div>
          </div>
          <Popconfirm
            title="确认退出登录？"
            description="退出后需要重新登录后台管理系统。"
            okText="确认退出"
            cancelText="取消"
            onConfirm={logout}
          >
            <span title="退出登录" style={{ display: 'inline-flex', alignItems: 'center' }}>
              <LogoutOutlined
                style={{ color: 'rgba(255,255,255,.45)', cursor: 'pointer', fontSize: 16 }}
              />
            </span>
          </Popconfirm>
        </div>
      </Sider>

      <Layout>
        <Header style={{
          background: '#fff',
          borderBottom: '1px solid #e2e8f0',
          height: 56,
          padding: '0 28px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}>
          <Space>
            <Text strong style={{ fontSize: 16 }}>{pageTitle}</Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              首页 / {pageTitle}
            </Text>
          </Space>
          <Space size={16}>
            <Tag color="success">在线</Tag>
            <Text type="secondary" style={{ fontSize: 12 }}>{time}</Text>
          </Space>
        </Header>

        <Content style={{ padding: '24px 28px', overflow: 'auto', background: '#f1f5f9' }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
