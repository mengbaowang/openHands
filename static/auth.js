/**
 * 登录/注册页面逻辑
 */

// Tab切换
const loginTabs = document.querySelectorAll('.login-tab');
const loginForm = document.getElementById('loginForm');
const registerForm = document.getElementById('registerForm');
const errorMessage = document.getElementById('errorMessage');

loginTabs.forEach(tab => {
    tab.addEventListener('click', () => {
        const tabName = tab.dataset.tab;
        
        // 切换tab样式
        loginTabs.forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        
        // 切换表单
        if (tabName === 'login') {
            loginForm.classList.add('active');
            registerForm.classList.remove('active');
        } else {
            loginForm.classList.remove('active');
            registerForm.classList.add('active');
        }
        
        // 清除错误信息
        hideError();
    });
});

// 登录表单提交
loginForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const username = document.getElementById('loginUsername').value.trim();
    const password = document.getElementById('loginPassword').value;
    
    if (!username || !password) {
        showError('请输入用户名和密码');
        return;
    }
    
    if (password.length < 6) {
        showError('密码至少6位');
        return;
    }
    
    try {
        const response = await fetch('/api/auth/login', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            credentials: 'include',
            body: JSON.stringify({ username, password })
        });
        
        const data = await response.json();
        
        if (response.ok) {
            // 登录成功，跳转到仪表板
            window.location.href = '/dashboard';
        } else {
            showError(data.error || '登录失败');
        }
    } catch (error) {
        showError('网络错误，请稍后重试');
        console.error('Login error:', error);
    }
});

// 注册表单提交
registerForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const username = document.getElementById('registerUsername').value.trim();
    const password = document.getElementById('registerPassword').value;
    const email = document.getElementById('registerEmail').value.trim();
    
    if (!username || !password) {
        showError('请输入用户名和密码');
        return;
    }
    
    if (username.length < 3) {
        showError('用户名至少3位');
        return;
    }
    
    if (password.length < 6) {
        showError('密码至少6位');
        return;
    }
    
    try {
        const response = await fetch('/api/auth/register', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            credentials: 'include',
            body: JSON.stringify({ username, password, email: email || null })
        });
        
        const data = await response.json();
        
        if (response.ok) {
            // 注册成功，跳转到仪表板
            window.location.href = '/dashboard';
        } else {
            showError(data.error || '注册失败');
        }
    } catch (error) {
        showError('网络错误，请稍后重试');
        console.error('Register error:', error);
    }
});

// 显示错误信息
function showError(message) {
    errorMessage.textContent = message;
    errorMessage.style.display = 'block';
}

// 隐藏错误信息
function hideError() {
    errorMessage.style.display = 'none';
}

