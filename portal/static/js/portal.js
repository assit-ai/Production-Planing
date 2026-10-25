// Production & Store Planning Enterprise Web Portal & Mobile App
// Real-time Controllers, Multi-Category, ERP Sync, UOM Toggle & RBAC

// Global fetch wrapper to ensure authentication tokens are always attached on Serverless/Vercel
(function() {
  const originalFetch = window.fetch;
  window.fetch = function(url, options = {}) {
    options = options || {};
    const token = sessionStorage.getItem('paragon_session_token') || localStorage.getItem('paragon_session_token');
    if (token) {
      if (!options.headers) {
        options.headers = {};
      }
      if (options.headers instanceof Headers) {
        if (!options.headers.has('Authorization')) options.headers.set('Authorization', 'Bearer ' + token);
        if (!options.headers.has('X-Session-Token')) options.headers.set('X-Session-Token', token);
      } else if (Array.isArray(options.headers)) {
        options.headers.push(['Authorization', 'Bearer ' + token]);
        options.headers.push(['X-Session-Token', token]);
      } else {
        options.headers['Authorization'] = options.headers['Authorization'] || ('Bearer ' + token);
        options.headers['X-Session-Token'] = options.headers['X-Session-Token'] || token;
      }
    }
    return originalFetch(url, options);
  };
})();

const AppState = {
  currentUser: {
    id: 1,
    username: 'admin',
    name: 'Super Admin (System Manager)',
    role: 'Super Admin',
    department: 'Management & IT',
    allowed_category: 'ALL'
  },
  currentView: 'dashboard',
  selectedCategory: '05', // '05' Dry Foods, '02' Frozen, '01' Chicken, 'ALL'
  selectedMonth: '2026-07',
  displayUOM: 'Packet', // 'Packet' (Default) or 'Carton'
  storeSubTab: 'inventory', // 'inventory' or 'requisitions'
  adminSubTab: 'skus',
  dashboardStats: null,
  skus: [],
  materials: [],
  charts: {},
  storeFilter: 'all',
  theme: localStorage.getItem('portal_theme') || 'light',
  reportFilterMode: 'month'
};

document.addEventListener('DOMContentLoaded', async () => {
  initTheme();
  initNavigation();
  const isLoggedIn = await initCurrentUser();
  if (isLoggedIn) {
    await initCategories();
    loadDashboard();
  }
});

// ----------------- THEME CONTROLLER -----------------
function initTheme() {
  if (!localStorage.getItem('portal_theme_v2_initialized')) {
    localStorage.setItem('portal_theme', 'light');
    localStorage.setItem('portal_theme_v2_initialized', 'true');
  }
  AppState.theme = localStorage.getItem('portal_theme') || 'light';
  document.documentElement.setAttribute('data-theme', AppState.theme);
  const icon = document.getElementById('theme-icon');
  if (icon) {
    icon.className = AppState.theme === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
  }
}

function toggleTheme() {
  AppState.theme = AppState.theme === 'dark' ? 'light' : 'dark';
  localStorage.setItem('portal_theme', AppState.theme);
  initTheme();
  if (AppState.currentView === 'dashboard') {
    renderDashboardCharts();
  }
}

// ----------------- AUTH & USER CONTROLLER -----------------
async function initCurrentUser() {
  // Clear persistent local storage so closing tab / opening bookmark always prompts for login
  localStorage.removeItem('paragon_session_user');
  localStorage.removeItem('paragon_session_token');

  const sessionUserStr = sessionStorage.getItem('paragon_session_user');
  const token = sessionStorage.getItem('paragon_session_token');
  const loginOverlay = document.getElementById('login-screen-overlay');
  
  if (!sessionUserStr || !token) {
    if (loginOverlay) {
      loginOverlay.classList.remove('hidden');
      setTimeout(() => {
        const uInp = document.getElementById('login-username');
        if (uInp) uInp.focus();
      }, 100);
    }
    return false;
  }
  
  try {
    const user = JSON.parse(sessionUserStr);
    AppState.currentUser = user;
    if (loginOverlay) loginOverlay.classList.add('hidden');
    updateUserUI();
    if (user.must_change_password) {
      setTimeout(() => {
        showToast('Security notice: Please change your default password.', 'warning');
        openChangePasswordModal(true);
      }, 700);
    }
    return true;
  } catch (e) {
    sessionStorage.removeItem('paragon_session_user');
    sessionStorage.removeItem('paragon_session_token');
    if (loginOverlay) loginOverlay.classList.remove('hidden');
    return false;
  }
}

function openChangePasswordModal(isFirstTime = false) {
  const alertBox = document.getElementById('first-time-password-alert');
  const errBox = document.getElementById('change-pass-error');
  
  if (alertBox) alertBox.style.display = isFirstTime ? 'block' : 'none';
  if (errBox) {
    errBox.style.display = 'none';
    errBox.textContent = '';
  }
  
  const oEl = document.getElementById('change-pass-old');
  const nEl = document.getElementById('change-pass-new');
  const cEl = document.getElementById('change-pass-confirm');
  if (oEl) oEl.value = '';
  if (nEl) nEl.value = '';
  if (cEl) cEl.value = '';
  
  showModal('modal-change-password');
}

async function submitChangePassword() {
  const oldPass = document.getElementById('change-pass-old')?.value.trim() || '';
  const newPass = document.getElementById('change-pass-new')?.value.trim() || '';
  const confirmPass = document.getElementById('change-pass-confirm')?.value.trim() || '';
  const errBox = document.getElementById('change-pass-error');

  const showError = (msg) => {
    if (errBox) {
      errBox.textContent = msg;
      errBox.style.display = 'block';
    }
  };

  if (!oldPass || !newPass) {
    showError('Please enter both current and new password.');
    return;
  }
  if (newPass.length < 6) {
    showError('New password must be at least 6 characters long.');
    return;
  }
  if (newPass !== confirmPass) {
    showError('Confirm password does not match new password.');
    return;
  }
  if (newPass === oldPass) {
    showError('New password must be different from current password.');
    return;
  }

  try {
    const res = await fetch('/api/auth/change-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ old_password: oldPass, new_password: newPass })
    });
    const data = await res.json();
    if (data.success) {
      if (AppState.currentUser) {
        AppState.currentUser.must_change_password = false;
        sessionStorage.setItem('paragon_session_user', JSON.stringify(AppState.currentUser));
      }
      hideModal('modal-change-password');
      showToast('Password changed successfully! (পাসওয়ার্ড সফলভাবে পরিবর্তন করা হয়েছে)', 'success');
    } else {
      showError(data.error || 'Failed to update password.');
    }
  } catch (e) {
    showError('Network error! Could not connect to server.');
  }
}

async function handleLoginSubmit() {
  const username = document.getElementById('login-username').value.trim();
  const password = document.getElementById('login-password').value.trim();
  const errBox = document.getElementById('login-error-alert');
  const errText = document.getElementById('login-error-text');

  if (!username || !password) {
    if (errBox) {
      errText.textContent = 'Please enter both User ID and Password!';
      errBox.style.display = 'flex';
    }
    return;
  }

  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    });
    const data = await res.json();
    if (data.success && data.user) {
      if (data.token) {
        sessionStorage.setItem('paragon_session_token', data.token);
      }
      sessionStorage.setItem('paragon_session_user', JSON.stringify(data.user));
      AppState.currentUser = data.user;
      
      const loginOverlay = document.getElementById('login-screen-overlay');
      if (loginOverlay) loginOverlay.classList.add('hidden');
      if (errBox) errBox.style.display = 'none';

      // Enforce category access
      if (data.user.allowed_category && data.user.allowed_category !== 'ALL') {
        AppState.selectedCategory = data.user.allowed_category;
      }

      updateUserUI();
      await initCategories();
      showToast(`Welcome, ${data.user.name} (${data.user.role})!`, 'success');
      
      // Always switch to dashboard on login so unauthorized views never persist
      switchView('dashboard');

      // Check first-time login notification for password change
      if (data.user.must_change_password) {
        setTimeout(() => {
          showToast('Security notice: Please change your default password.', 'warning');
          openChangePasswordModal(true);
        }, 500);
      }
    } else {
      if (errBox) {
        errText.textContent = data.error || 'Invalid User ID or Password!';
        errBox.style.display = 'flex';
      }
    }
  } catch (e) {
    if (errBox) {
      errText.textContent = 'Connection error! Could not reach server.';
      errBox.style.display = 'flex';
    }
  }
}

function quickFillLogin(username, password) {
  const uInp = document.getElementById('login-username');
  const pInp = document.getElementById('login-password');
  if (uInp && pInp) {
    uInp.value = username;
    pInp.value = password;
    handleLoginSubmit();
  }
}

async function logout() {
  sessionStorage.removeItem('paragon_session_user');
  sessionStorage.removeItem('paragon_session_token');
  localStorage.removeItem('paragon_session_user');
  localStorage.removeItem('paragon_session_token');
  try {
    await fetch('/api/auth/logout', { method: 'POST' });
  } catch (e) {}
  AppState.currentUser = null;
  AppState.currentView = 'dashboard';
  document.body.classList.remove('is-super-admin');
  document.querySelectorAll('.admin-only, .danger-zone-card').forEach(el => {
    el.style.setProperty('display', 'none', 'important');
  });
  
  // Reset all views so next user always starts on dashboard
  document.querySelectorAll('.view-section').forEach(sec => sec.classList.remove('active'));
  const dSec = document.getElementById('view-dashboard');
  if (dSec) dSec.classList.add('active');

  document.querySelectorAll('.nav-item, .mobile-nav-item').forEach(item => {
    item.classList.toggle('active', item.getAttribute('data-view') === 'dashboard');
  });

  const titleEl = document.getElementById('page-header-title');
  if (titleEl) titleEl.textContent = 'Executive Dashboard & Overview';

  const loginOverlay = document.getElementById('login-screen-overlay');
  if (loginOverlay) {
    loginOverlay.classList.remove('hidden');
    const pInp = document.getElementById('login-password');
    if (pInp) pInp.value = '';
  }
  showToast('You have been logged out.', 'primary');
}

function updateUserUI() {
  if (!AppState.currentUser) return;
  const u = AppState.currentUser;
  
  const nameEl = document.getElementById('user-display-name');
  const roleEl = document.getElementById('user-display-role');
  const avatarEl = document.getElementById('user-avatar-badge');
  const topName = document.getElementById('top-user-name');
  const topRole = document.getElementById('top-user-role');
  const lockBadge = document.getElementById('category-locked-badge');
  const lockText = document.getElementById('category-locked-text');
  const catSelect = document.getElementById('category-select-box');

  if (nameEl) nameEl.textContent = u.name;
  if (roleEl) roleEl.textContent = `${u.role} • ${u.allowed_category === 'ALL' ? 'All Units' : 'Unit ' + u.allowed_category}`;
  if (avatarEl) avatarEl.textContent = u.name.charAt(0);
  if (topName) topName.textContent = u.name;
  if (topRole) topRole.textContent = `(${u.role})`;

  // Category restriction enforcement
  if (u.allowed_category && u.allowed_category !== 'ALL') {
    AppState.selectedCategory = u.allowed_category;
    if (lockBadge) {
      lockBadge.style.display = 'inline-flex';
      lockText.textContent = `Unit ${u.allowed_category} Only`;
    }
    if (catSelect) {
      catSelect.value = u.allowed_category;
      catSelect.disabled = true;
      catSelect.title = `Your account is restricted to Unit ${u.allowed_category}`;
    }
  } else {
    if (lockBadge) lockBadge.style.display = 'none';
    if (catSelect) {
      catSelect.disabled = false;
      catSelect.title = 'Select Category / Unit';
    }
  }

  applyRolePermissions();
}

function isSuperAdminUser() {
  const role = AppState.currentUser?.role;
  return role === 'Super Admin' || role === 'Admin';
}

function canUserEdit(section) {
  if (!AppState.currentUser) return false;
  if (isSuperAdminUser()) return true;

  const role = AppState.currentUser.role;
  if (section === 'forecast') {
    return role === 'Sales';
  } else if (section === 'production') {
    return role === 'Production';
  } else if (section === 'store' || section === 'mrr') {
    return role === 'Store';
  } else if (section === 'wastage') {
    return role === 'Production' || role === 'Store';
  } else if (section === 'procurement') {
    return role === 'Store' || role === 'Supply Chain';
  }
  return false;
}

function applyRolePermissions() {
  if (!AppState.currentUser) {
    document.body.classList.remove('is-super-admin');
    document.querySelectorAll('.admin-only, .danger-zone-card').forEach(el => {
      el.style.setProperty('display', 'none', 'important');
    });
    return;
  }
  const isSuper = isSuperAdminUser();
  if (isSuper) {
    document.body.classList.add('is-super-admin');
  } else {
    document.body.classList.remove('is-super-admin');
  }
  
  // Super Admin only elements (e.g. Master & BOM, User Management, Add Category, Poloxy ERP settings, Turso Cloud DB, Data Cleanup Danger Zone)
  const adminEls = document.querySelectorAll('.admin-only, .danger-zone-card');
  adminEls.forEach(el => {
    if (isSuper) {
      if (el.classList.contains('nav-item')) {
        el.style.setProperty('display', 'flex', 'important');
      } else {
        el.style.setProperty('display', 'block', 'important');
      }
    } else {
      el.style.setProperty('display', 'none', 'important');
    }
  });

  // 1. Sales Forecast Section Edit Buttons
  const canEditForecast = canUserEdit('forecast');
  const saveForecastBtn = document.querySelector('[onclick="saveForecastChanges()"]');
  const bulkForecastBtn = document.querySelector('[onclick="openBulkForecastModal()"]');
  const smartPlanBtn = document.querySelector('[onclick="openSmartPlanModal()"]');
  if (saveForecastBtn) saveForecastBtn.style.display = canEditForecast ? '' : 'none';
  if (bulkForecastBtn) bulkForecastBtn.style.display = canEditForecast ? '' : 'none';
  if (smartPlanBtn) smartPlanBtn.style.display = canEditForecast ? '' : 'none';

  // 2. Production Section Edit Buttons
  const canEditProduction = canUserEdit('production');
  const createPlanBtn = document.querySelector('[onclick="openCreatePlanModal()"]');
  const bulkProdBtn = document.querySelector('[onclick="openBulkProductionModal()"]');
  const poloxyBtn = document.querySelector('[onclick="openPoloxySyncModal()"]') || document.querySelector('[onclick="syncFromPoloxyERP()"]');
  if (createPlanBtn) createPlanBtn.style.display = canEditProduction ? '' : 'none';
  if (bulkProdBtn) bulkProdBtn.style.display = canEditProduction ? '' : 'none';
  if (poloxyBtn) poloxyBtn.style.display = canEditProduction ? '' : 'none';

  // 3. Store Section Edit Buttons
  const canEditStore = canUserEdit('store');
  const mrrBtn = document.querySelector('[onclick="openCreateMRRModal()"]');
  const replenishBtn = document.querySelector('[onclick="triggerAutoReplenishment()"]');
  if (mrrBtn) mrrBtn.style.display = canEditStore ? '' : 'none';
  if (replenishBtn) replenishBtn.style.display = canEditStore ? '' : 'none';

  // 4. Wastage Section
  const wastageBtn = document.querySelector('[onclick="openCreateWastageModal()"]');
  if (wastageBtn) wastageBtn.style.display = canUserEdit('wastage') ? '' : 'none';

  // 5. Read-Only Notification Badges
  const forecastTag = document.getElementById('forecast-readonly-tag');
  if (forecastTag) forecastTag.style.display = canEditForecast ? 'none' : 'inline-flex';

  const productionTag = document.getElementById('production-readonly-tag');
  if (productionTag) productionTag.style.display = canEditProduction ? 'none' : 'inline-flex';

  const storeTag = document.getElementById('store-readonly-tag');
  if (storeTag) storeTag.style.display = canEditStore ? 'none' : 'inline-flex';
}

// ----------------- CATEGORY CONTROLLER -----------------
async function initCategories() {
  try {
    const res = await fetch('/api/categories');
    const cats = await res.json();
    const select = document.getElementById('category-select-box');
    if (select) {
      select.innerHTML = cats.map(c => 
        `<option value="${c.code}" ${c.code === AppState.selectedCategory ? 'selected' : ''}>🏢 ${c.name}</option>`
      ).join('') + `<option value="ALL">🌐 All Categories / Units</option>` +
      `<option value="NEW">+ Add New Category...</option>`;
    }
  } catch (e) {
    console.error('Error loading categories:', e);
  }
}

function handleCategoryChange(val) {
  if (val === 'NEW') {
    openAddCategoryModal();
    const select = document.getElementById('category-select-box');
    select.value = AppState.selectedCategory;
    return;
  }
  AppState.selectedCategory = val;
  showToast(`Switched view to Unit / Category: ${val}`, 'primary');
  switchView(AppState.currentView);
}

function openAddCategoryModal() {
  document.getElementById('form-add-category')?.reset();
  showModal('modal-add-category');
}

async function submitAddCategory() {
  const code = document.getElementById('new-cat-code').value.trim();
  const name = document.getElementById('new-cat-name').value.trim();
  const wc = document.getElementById('new-cat-work-center').value.trim();
  const branch = document.getElementById('new-cat-branch').value.trim();
  const desc = document.getElementById('new-cat-desc').value.trim();

  if (!code || !name) {
    showToast('Code and Name are required', 'warning');
    return;
  }

  try {
    const res = await fetch('/api/categories', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, name, work_center: wc, branch_id: branch, description: desc })
    });
    const data = await res.json();
    if (data.success) {
      hideModal('modal-add-category');
      showToast(`Category ${name} added successfully!`, 'success');
      AppState.selectedCategory = code;
      await initCategories();
      switchView(AppState.currentView);
    }
  } catch (e) {
    showToast('Failed to add category', 'danger');
  }
}

// ----------------- UOM TOGGLE (Packet vs Carton) -----------------
function setDisplayUOM(uom) {
  AppState.displayUOM = uom;
  document.querySelectorAll('.uom-btn').forEach(b => {
    b.classList.toggle('active', b.getAttribute('data-uom') === uom);
  });
  showToast(`Display unit changed to: ${uom}s`, 'primary');
  switchView(AppState.currentView);
}

// ----------------- NAVIGATION & ROUTING -----------------
function initNavigation() {
  document.querySelectorAll('.nav-item, .mobile-nav-item').forEach(item => {
    item.addEventListener('click', (e) => {
      e.preventDefault();
      const view = item.getAttribute('data-view');
      if (view) {
        switchView(view);
        document.querySelector('.sidebar').classList.remove('show');
      }
    });
  });

  const menuBtn = document.getElementById('mobile-menu-toggle');
  if (menuBtn) {
    menuBtn.addEventListener('click', () => {
      document.querySelector('.sidebar').classList.toggle('show');
    });
  }
}

function switchView(viewName, subOptions = {}) {
  // Access control: Only Super Admin can view 'users' or 'master_bom'
  if ((viewName === 'users' || viewName === 'master_bom') && !isSuperAdminUser()) {
    showToast('Access Denied: Only Super Admin has permission to view this section.', 'warning');
    viewName = 'dashboard';
  }

  AppState.currentView = viewName;

  document.querySelectorAll('.nav-item, .mobile-nav-item').forEach(item => {
    if (item.getAttribute('data-view') === viewName) {
      item.classList.add('active');
    } else {
      item.classList.remove('active');
    }
  });

  document.querySelectorAll('.view-section').forEach(sec => {
    sec.classList.remove('active');
  });
  const targetSec = document.getElementById(`view-${viewName}`);
  if (targetSec) {
    targetSec.classList.add('active');
  }

  const titles = {
    'dashboard': 'Executive Dashboard & Overview',
    'forecast': 'Monthly Sales Forecast Entry & Enlistment',
    'production': 'Production Planning & Material Requirement',
    'store': 'Store Inventory, Stock Inward (MRR) & Safety Stock',
    'procurement': 'Supply Chain & Purchase Orders (PO)',
    'master_bom': 'Master Data, BOM Formulation & User Management',
    'reports': 'Reports & Analytics (Performance, Stock & FSN)',
    'users': 'System User Management & Access Control (RBAC)'
  };
  const titleEl = document.getElementById('page-header-title');
  if (titleEl) titleEl.textContent = titles[viewName] || 'Planning Portal';

  // Handle specific view & sub-tab parameters
  switch (viewName) {
    case 'dashboard': loadDashboard(); break;
    case 'forecast': loadForecastView(); break;
    case 'production': loadProductionView(); break;
    case 'store': 
      const targetSubTab = subOptions.subTab || 'inventory';
      const targetFilter = subOptions.filter || 'all';
      switchStoreSubTab(targetSubTab);
      setStoreFilter(targetFilter);
      break;
    case 'procurement': loadProcurementView(); break;
    case 'master_bom': 
      if (subOptions.adminTab) setAdminTab(subOptions.adminTab);
      loadMasterBOMView(); 
      break;
    case 'reports': loadReportsView(); break;
    case 'users': loadAdminUsers(); break;
  }
  applyRolePermissions();
}

// ----------------- 1. DASHBOARD CONTROLLER -----------------
async function loadDashboard() {
  const cat = AppState.selectedCategory;
  const month = AppState.selectedMonth;
  const uom = AppState.displayUOM;

  try {
    const res = await fetch(`/api/dashboard/stats?category=${cat}&month=${month}&uom=${uom}`);
    const stats = await res.json();
    AppState.dashboardStats = stats;

    document.getElementById('kpi-total-items').textContent = stats.materials.total;
    document.getElementById('kpi-rm-pm-count').textContent = `${stats.materials.rm_count} RM / ${stats.materials.pm_count} PM`;

    document.getElementById('kpi-critical-items').textContent = stats.materials.critical;
    document.getElementById('kpi-low-stock-count').textContent = `${stats.materials.low} items near safety buffer`;

    document.getElementById('kpi-pending-prod-req').textContent = stats.requisitions.pending_production;
    document.getElementById('kpi-pending-po').textContent = `${stats.requisitions.pending_purchase} POs in Procurement`;

    document.getElementById('kpi-inventory-val').textContent = `৳ ${stats.valuation.total.toLocaleString()}`;
    document.getElementById('kpi-rm-pm-val').textContent = `RM: ৳${stats.valuation.rm.toLocaleString()} | PM: ৳${stats.valuation.pm.toLocaleString()}`;

    document.getElementById('kpi-achievement').textContent = `${stats.performance.achievement_pct}%`;
    document.getElementById('kpi-actual-produced').textContent = `${stats.performance.actual.toLocaleString()} / ${stats.performance.planned.toLocaleString()} ${uom}s`;

    // Sidebar badges
    const critBadge = document.getElementById('badge-critical-stock');
    if (critBadge) critBadge.textContent = stats.materials.critical;

    const prodReqBadge = document.getElementById('badge-prod-req');
    if (prodReqBadge) prodReqBadge.textContent = stats.requisitions.pending_production;

    const poBadge = document.getElementById('badge-po-req');
    if (poBadge) poBadge.textContent = stats.requisitions.pending_purchase;

    renderCriticalTable(stats.critical_items);
    renderRecentTxns(stats.recent_transactions);
    renderDashboardCharts();
  } catch (e) {
    console.error('Error loading dashboard:', e);
  }
}

function renderCriticalTable(items) {
  const tbody = document.getElementById('dashboard-critical-table-body');
  if (!tbody) return;

  if (!items || items.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:1.5rem; color:var(--text-dim);">
      <i class="fas fa-check-circle" style="color:var(--success); margin-right:6px;"></i> All items currently maintain healthy safety stock buffers!
    </td></tr>`;
    return;
  }

  tbody.innerHTML = items.map(it => `
    <tr>
      <td><strong>${it.item_code}</strong></td>
      <td>
        <div style="font-weight:600;">${it.name}</div>
        <div style="font-size:0.72rem; color:var(--text-dim);">${it.supplier_name || 'Standard Supplier'}</div>
      </td>
      <td><span class="pill ${it.type === 'RM' ? 'pill-safe' : 'pill-fast'}">${it.type}</span></td>
      <td><span style="color:var(--danger); font-weight:700;">${it.current_stock.toLocaleString()}</span> ${it.unit}</td>
      <td><strong>${it.safety_stock.toLocaleString()}</strong> ${it.unit}</td>
      <td><span class="pill pill-critical">Deficit: -${it.deficit} ${it.unit}</span></td>
      <td>
        <button class="btn btn-sm btn-primary" onclick="openRaisePurchaseModal(${it.id}, '${it.name.replace(/'/g, "\\'")}', ${it.deficit * 2}, ${it.unit_price}, '${it.supplier_name}')">
          <i class="fas fa-paper-plane"></i> Requisition
        </button>
      </td>
    </tr>
  `).join('');
}

function renderRecentTxns(txns) {
  const container = document.getElementById('dashboard-recent-txns');
  if (!container) return;

  if (!txns || txns.length === 0) {
    container.innerHTML = `<div style="text-align:center; padding:1.5rem; color:var(--text-dim);">No recent store transactions.</div>`;
    return;
  }

  container.innerHTML = txns.map(t => {
    const isIssue = t.txn_type === 'ISSUE_PRODUCTION' || t.txn_type === 'WASTAGE';
    const isGRN = t.txn_type === 'RECEIPT_GRN' || t.txn_type === 'MRR_ENTRY';
    const icon = isIssue ? 'fa-arrow-up' : (isGRN ? 'fa-arrow-down' : 'fa-sync-alt');
    const color = isIssue ? 'var(--warning)' : (isGRN ? 'var(--success)' : 'var(--cyan)');
    const sign = t.qty > 0 ? '+' : '';

    return `
      <div style="display:flex; align-items:center; justify-content:space-between; padding:0.65rem 0; border-bottom:1px solid var(--card-border);">
        <div style="display:flex; align-items:center; gap:0.65rem;">
          <div style="width:32px; height:32px; border-radius:8px; background:var(--bg-tertiary); display:flex; align-items:center; justify-content:center; color:${color}; font-size:0.8rem;">
            <i class="fas ${icon}"></i>
          </div>
          <div>
            <div style="font-size:0.82rem; font-weight:600;">${t.material_name}</div>
            <div style="font-size:0.7rem; color:var(--text-dim);">${t.notes || t.reference_no} • ${t.created_by}</div>
          </div>
        </div>
        <div style="text-align:right;">
          <div style="font-weight:700; color:${color}; font-size:0.82rem;">${sign}${t.qty.toLocaleString()} ${t.unit}</div>
          <div style="font-size:0.68rem; color:var(--text-dim);">Bal: ${t.balance_after.toLocaleString()}</div>
        </div>
      </div>
    `;
  }).join('');
}

function renderDashboardCharts() {
  if (!AppState.dashboardStats) return;
  const stats = AppState.dashboardStats;
  const isLight = AppState.theme === 'light';
  const gridColor = isLight ? '#e2e8f0' : 'rgba(255,255,255,0.06)';
  const textColor = isLight ? '#334155' : '#9ca3af';
  const labelColor = isLight ? '#0f172a' : '#f8fafc';

  // 1. BAR CHART: Forecast vs Plan vs Actual
  const ctxPerf = document.getElementById('chart-forecast-vs-prod');
  if (ctxPerf) {
    if (AppState.charts.perf) AppState.charts.perf.destroy();

    // Custom Data Labels Plugin for Bars
    const barLabelsPlugin = {
      id: 'barLabelsPlugin',
      afterDatasetsDraw(chart) {
        const { ctx } = chart;
        ctx.save();
        chart.data.datasets.forEach((dataset, i) => {
          const meta = chart.getDatasetMeta(i);
          meta.data.forEach((bar, index) => {
            const val = dataset.data[index];
            if (val === undefined || val === null) return;
            const text = Number(val).toLocaleString() + (AppState.displayUOM ? ` ${AppState.displayUOM}` : '');
            
            ctx.font = 'bold 11px Inter, system-ui, sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'bottom';
            ctx.fillStyle = labelColor;
            
            // Draw slightly above bar top
            const yPos = Math.max(bar.y - 6, 16);
            ctx.fillText(text, bar.x, yPos);
          });
        });
        ctx.restore();
      }
    };

    AppState.charts.perf = new Chart(ctxPerf, {
      type: 'bar',
      plugins: [barLabelsPlugin],
      data: {
        labels: ['Sales Forecast', 'Production Plan', 'Actual Produced'],
        datasets: [{
          label: `${AppState.displayUOM}s`,
          data: [
            stats.performance.forecast,
            stats.performance.planned,
            stats.performance.actual
          ],
          backgroundColor: [
            'rgba(59, 130, 246, 0.85)',
            'rgba(139, 92, 246, 0.85)',
            'rgba(16, 185, 129, 0.85)'
          ],
          borderColor: ['#3b82f6', '#8b5cf6', '#10b981'],
          borderWidth: 1.5,
          borderRadius: 8
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        layout: {
          padding: { top: 22, bottom: 6, left: 6, right: 6 }
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => ` ${ctx.dataset.label}: ${Number(ctx.raw).toLocaleString()}`
            }
          }
        },
        scales: {
          y: {
            beginAtZero: true,
            grace: '15%', // Room for top data labels
            ticks: { 
              color: textColor,
              callback: (val) => Number(val).toLocaleString()
            },
            grid: { color: gridColor }
          },
          x: {
            ticks: { color: textColor, font: { weight: '600' } },
            grid: { display: false }
          }
        }
      }
    });
  }

  // 2. DOUGHNUT CHART: Stock Health Breakdown
  const ctxHealth = document.getElementById('chart-stock-health');
  if (ctxHealth) {
    if (AppState.charts.health) AppState.charts.health.destroy();

    // Custom Data Labels Plugin for Doughnut Slices
    const doughnutLabelsPlugin = {
      id: 'doughnutLabelsPlugin',
      afterDatasetsDraw(chart) {
        const { ctx, data } = chart;
        ctx.save();
        const dataset = data.datasets[0];
        const meta = chart.getDatasetMeta(0);
        const total = dataset.data.reduce((a, b) => a + (Number(b) || 0), 0);

        meta.data.forEach((element, index) => {
          const val = dataset.data[index];
          if (!val || val <= 0) return;
          const pct = total > 0 ? Math.round((val / total) * 100) : 0;
          const pos = element.tooltipPosition();

          ctx.font = 'bold 12px Inter, system-ui, sans-serif';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillStyle = '#ffffff';
          ctx.shadowColor = 'rgba(0,0,0,0.7)';
          ctx.shadowBlur = 4;
          ctx.fillText(`${val} (${pct}%)`, pos.x, pos.y);
        });
        ctx.restore();
      }
    };

    AppState.charts.health = new Chart(ctxHealth, {
      type: 'doughnut',
      plugins: [doughnutLabelsPlugin],
      data: {
        labels: ['Safe Buffer', 'Low Stock', 'Critical (< Safety)'],
        datasets: [{
          data: [
            stats.materials.safe,
            stats.materials.low,
            stats.materials.critical
          ],
          backgroundColor: ['#10b981', '#f59e0b', '#ef4444'],
          borderWidth: 2,
          borderColor: isLight ? '#ffffff' : '#111827'
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '68%',
        plugins: {
          legend: {
            position: 'bottom',
            labels: { boxWidth: 12, font: { size: 11, weight: '600' }, color: textColor, padding: 14 }
          }
        }
      }
    });
  }
}

// ----------------- 2. SALES FORECAST CONTROLLER -----------------
async function loadForecastView() {
  const month = document.getElementById('forecast-month-select')?.value || AppState.selectedMonth;
  const cat = AppState.selectedCategory;
  const uom = AppState.displayUOM;

  try {
    const res = await fetch(`/api/forecast?category=${cat}&month=${month}&uom=${uom}`);
    const data = await res.json();

    const select = document.getElementById('forecast-month-select');
    if (select && select.children.length <= 1) {
      select.innerHTML = data.available_months.map(m => 
        `<option value="${m}" ${m === month ? 'selected' : ''}>📅 ${m}</option>`
      ).join('');
    }

    renderForecastTable(data.items);
    applyRolePermissions();
  } catch (e) {
    console.error('Error loading forecast:', e);
  }
}

function renderForecastTable(items) {
  const tbody = document.getElementById('forecast-table-body');
  if (!tbody) return;

  const canEdit = canUserEdit('forecast');
  const isSuperAdmin = AppState.currentUser?.role === 'Super Admin';
  let totalCtn = 0;
  let totalPcs = 0;

  tbody.innerHTML = items.map((it, idx) => {
    totalCtn += (it.forecast_ctn || 0);
    totalPcs += (it.forecast_pcs || 0);

    const origCtn = it.original_forecast_ctn != null ? it.original_forecast_ctn : (it.forecast_ctn || 0);
    const revNo = it.revision_no || 0;
    const varianceFromOrig = (it.forecast_ctn || 0) - origCtn;
    
    // Default weekly split if not set
    const totalCurrent = it.forecast_ctn || 0;
    const defaultW = Math.round(totalCurrent / 4);
    const w1 = it.week1_ctn != null ? it.week1_ctn : defaultW;
    const w2 = it.week2_ctn != null ? it.week2_ctn : defaultW;
    const w3 = it.week3_ctn != null ? it.week3_ctn : defaultW;
    const w4 = it.week4_ctn != null ? it.week4_ctn : (totalCurrent - (w1 + w2 + w3));

    return `
      <tr>
        <td>${idx + 1}</td>
        <td><strong>${it.sku_code}</strong></td>
        <td>
          <div style="font-weight:600;">${it.sku_name}</div>
          <div style="font-size:0.72rem; color:var(--text-dim);">${it.pack_size || ''} • ${it.ctn_size} ${it.uom_default || 'Pcs'}/Ctn</div>
        </td>
        <td><span class="pill pill-safe">${it.category}</span></td>
        <td style="text-align:right; color:var(--text-muted); font-weight:600;">
          ${origCtn.toLocaleString()} Ctn
        </td>
        <td>
          <input type="number" 
                 class="form-control forecast-input" 
                 style="width:115px; text-align:right; font-weight:700; ${canEdit ? '' : 'background:var(--bg-tertiary); opacity:0.8; cursor:not-allowed;'}" 
                 data-sku-id="${it.sku_id}" 
                 data-ctn-size="${it.ctn_size}"
                 data-orig-ctn="${origCtn}"
                 value="${it.forecast_ctn || 0}" 
                 min="0"
                 ${canEdit ? '' : 'disabled readonly'}
                 onchange="recalculateForecastRow(this)">
          ${varianceFromOrig !== 0 ? `
            <div style="font-size:0.68rem; text-align:right; margin-top:2px; font-weight:700; color:${varianceFromOrig > 0 ? 'var(--success)' : 'var(--danger)'};">
              ${varianceFromOrig > 0 ? '+' : ''}${varianceFromOrig} Ctn
            </div>
          ` : ''}
        </td>
        <td style="text-align:center;">
          ${revNo > 0 ? `
            <button type="button" class="btn btn-xs pill pill-warn" onclick="openForecastRevisionsModal(${it.sku_id}, '${it.sku_name.replace(/'/g, "\\'")}')" style="cursor:pointer; border:none;" title="Click to view full revision history">
              Rev ${revNo} <i class="fas fa-history" style="font-size:0.65rem; margin-left:2px;"></i>
            </button>
          ` : `
            <span class="pill pill-safe" style="font-size:0.72rem;">Rev 0 (Base)</span>
          `}
        </td>
        <td style="text-align:center;">
          <div style="display:inline-flex; gap:3px; align-items:center;">
            <input type="number" class="form-control form-control-sm forecast-w1" style="width:48px; font-size:0.72rem; padding:2px 3px; text-align:right;" data-sku-id="${it.sku_id}" value="${w1}" min="0" placeholder="W1" title="Week 1 (Ctn)" ${canEdit ? '' : 'disabled'} onchange="recalculateFromWeekly(this)">
            <input type="number" class="form-control form-control-sm forecast-w2" style="width:48px; font-size:0.72rem; padding:2px 3px; text-align:right;" data-sku-id="${it.sku_id}" value="${w2}" min="0" placeholder="W2" title="Week 2 (Ctn)" ${canEdit ? '' : 'disabled'} onchange="recalculateFromWeekly(this)">
            <input type="number" class="form-control form-control-sm forecast-w3" style="width:48px; font-size:0.72rem; padding:2px 3px; text-align:right;" data-sku-id="${it.sku_id}" value="${w3}" min="0" placeholder="W3" title="Week 3 (Ctn)" ${canEdit ? '' : 'disabled'} onchange="recalculateFromWeekly(this)">
            <input type="number" class="form-control form-control-sm forecast-w4" style="width:48px; font-size:0.72rem; padding:2px 3px; text-align:right;" data-sku-id="${it.sku_id}" value="${w4}" min="0" placeholder="W4" title="Week 4 (Ctn)" ${canEdit ? '' : 'disabled'} onchange="recalculateFromWeekly(this)">
          </div>
        </td>
        <td class="forecast-pcs-display" style="text-align:right; font-weight:700; color:var(--primary);">
          ${(it.forecast_pcs || 0).toLocaleString()} ${it.uom_default || 'Packets'}
        </td>
        <td style="text-align:center; white-space:nowrap;">
          <div style="display:inline-flex; gap:4px;">
            ${isSuperAdmin ? `
              <button class="btn btn-xs btn-outline" onclick="openForecastBOMModal(${it.sku_id}, '${it.sku_name.replace(/'/g, "\\'")}')" title="View Bill of Materials Specification (Super Admin Only)">
                <i class="fas fa-sitemap" style="color:var(--primary);"></i> BOM
              </button>
            ` : `
              <span class="pill pill-low" style="font-size:0.68rem; opacity:0.65; cursor:not-allowed;" title="BOM access is strictly restricted to Super Admin only">
                <i class="fas fa-lock" style="font-size:0.65rem;"></i> Locked
              </span>
            `}
            <button class="btn btn-xs btn-outline" onclick="openForecastRevisionsModal(${it.sku_id}, '${it.sku_name.replace(/'/g, "\\'")}')" title="View Revision History">
              <i class="fas fa-history"></i> Log
            </button>
          </div>
        </td>
      </tr>
    `;
  }).join('');

  document.getElementById('forecast-total-cartons').textContent = totalCtn.toLocaleString();
  document.getElementById('forecast-total-pieces').textContent = totalPcs.toLocaleString();
}

function recalculateFromWeekly(weeklyInp) {
  const row = weeklyInp.closest('tr');
  const w1 = parseFloat(row.querySelector('.forecast-w1')?.value) || 0;
  const w2 = parseFloat(row.querySelector('.forecast-w2')?.value) || 0;
  const w3 = parseFloat(row.querySelector('.forecast-w3')?.value) || 0;
  const w4 = parseFloat(row.querySelector('.forecast-w4')?.value) || 0;
  const total = w1 + w2 + w3 + w4;

  const mainInp = row.querySelector('.forecast-input');
  if (mainInp) {
    mainInp.value = total;
    recalculateForecastRow(mainInp, false);
  }
}

function recalculateForecastRow(input, autoSplitWeekly = true) {
  const ctn = parseFloat(input.value) || 0;
  const ctnSize = parseFloat(input.getAttribute('data-ctn-size')) || 1;
  const row = input.closest('tr');
  const pcsDisplay = row.querySelector('.forecast-pcs-display');
  if (pcsDisplay) {
    pcsDisplay.textContent = `${(ctn * ctnSize).toLocaleString()} Packets`;
  }

  // Update weekly inputs if autoSplitWeekly is requested
  if (autoSplitWeekly && row) {
    const w1 = row.querySelector('.forecast-w1');
    const w2 = row.querySelector('.forecast-w2');
    const w3 = row.querySelector('.forecast-w3');
    const w4 = row.querySelector('.forecast-w4');
    const baseW = Math.round(ctn / 4);
    if (w1) w1.value = baseW;
    if (w2) w2.value = baseW;
    if (w3) w3.value = baseW;
    if (w4) w4.value = Math.max(0, ctn - (baseW * 3));
  }

  let grandCtn = 0;
  let grandPcs = 0;
  document.querySelectorAll('.forecast-input').forEach(inp => {
    const c = parseFloat(inp.value) || 0;
    const s = parseFloat(inp.getAttribute('data-ctn-size')) || 1;
    grandCtn += c;
    grandPcs += c * s;
  });
  document.getElementById('forecast-total-cartons').textContent = grandCtn.toLocaleString();
  document.getElementById('forecast-total-pieces').textContent = grandPcs.toLocaleString();
}

async function saveForecastChanges() {
  const month = document.getElementById('forecast-month-select').value;
  const items = [];
  let hasChanges = false;

  document.querySelectorAll('.forecast-input').forEach(inp => {
    const sku_id = parseInt(inp.getAttribute('data-sku-id'));
    const forecast_ctn = parseFloat(inp.value) || 0;
    const orig_ctn = parseFloat(inp.getAttribute('data-orig-ctn')) || 0;

    const row = inp.closest('tr');
    const week1_ctn = parseFloat(row.querySelector('.forecast-w1')?.value) || 0;
    const week2_ctn = parseFloat(row.querySelector('.forecast-w2')?.value) || 0;
    const week3_ctn = parseFloat(row.querySelector('.forecast-w3')?.value) || 0;
    const week4_ctn = parseFloat(row.querySelector('.forecast-w4')?.value) || 0;

    if (forecast_ctn !== orig_ctn) {
      hasChanges = true;
    }

    items.push({
      sku_id,
      forecast_ctn,
      week1_ctn,
      week2_ctn,
      week3_ctn,
      week4_ctn
    });
  });

  let revisionNotes = 'Monthly Sales Forecast Update';
  if (hasChanges) {
    const userReason = prompt('Sales forecast values have been adjusted. Please enter revision reason or specific period note (যেমনঃ Week 3 Promotion, Ramadan surge):', 'Periodic Demand Adjustment');
    if (userReason !== null && userReason.trim() !== '') {
      revisionNotes = userReason.trim();
    }
  }

  const saveBtn = document.querySelector('[onclick="saveForecastChanges()"]');
  if (saveBtn) {
    saveBtn.disabled = true;
    saveBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving to Cloud...';
  }
  showToast('Saving forecast changes & calculating weekly allocations...', 'info');

  try {
    const res = await fetch('/api/forecast', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ 
        month, 
        items,
        revision_notes: revisionNotes,
        revised_by: AppState.currentUser?.name || 'Sales Team'
      })
    });
    const data = await res.json();
    if (data.success) {
      const countMsg = data.items_updated || data.count || items.length;
      showToast(`✓ Sales forecast successfully saved (${countMsg} SKUs)!`, 'success');
      loadForecastView();
      loadDashboard();

      if (hasChanges) {
        showToast('Tip: Go to Production Plans and click "Auto-Align Plans" to sync future daily targets with this revised forecast.', 'info');
      }
    } else {
      showToast(data.error || 'Failed to save sales forecast', 'danger');
    }
  } catch (e) {
    showToast('Failed to save sales forecast. Please check network connection.', 'danger');
  } finally {
    if (saveBtn) {
      saveBtn.disabled = false;
      saveBtn.innerHTML = '<i class="fas fa-save"></i> Save Forecast Changes';
    }
  }
}

async function openForecastRevisionsModal(skuId, skuName) {
  const month = document.getElementById('forecast-month-select')?.value || AppState.selectedMonth;
  const titleEl = document.getElementById('modal-revision-sku-title');
  const subEl = document.getElementById('modal-revision-sku-sub');
  if (titleEl) titleEl.textContent = `${skuName} - Forecast Revision History`;
  if (subEl) subEl.textContent = `Full audit log of all weekly/monthly adjustments made for ${month} (SKU #${skuId}).`;

  const tbody = document.getElementById('modal-forecast-revisions-tbody');
  if (tbody) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding:1.5rem;"><i class="fas fa-spinner fa-spin"></i> Loading revision log...</td></tr>';
  }
  showModal('modal-forecast-revisions');

  try {
    const res = await fetch(`/api/forecast/revisions?sku_id=${skuId}&month=${month}`);
    const data = await res.json();
    if (!tbody) return;

    const revList = Array.isArray(data) ? data : (data.revisions || []);
    if (revList.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--text-muted); padding:1.5rem;">No revisions found. The base initial forecast is currently active.</td></tr>';
      return;
    }

    tbody.innerHTML = revList.map(r => {
      const varVal = r.variance_ctn || (r.revised_ctn - r.previous_ctn);
      return `
        <tr>
          <td style="text-align:center;"><span class="pill ${r.revision_no > 0 ? 'pill-warn' : 'pill-safe'}">Rev ${r.revision_no}</span></td>
          <td>${r.revised_date || 'N/A'}</td>
          <td style="text-align:right; font-weight:600;">${(r.previous_ctn || 0).toLocaleString()}</td>
          <td style="text-align:right; font-weight:700; color:var(--primary);">${(r.revised_ctn || 0).toLocaleString()}</td>
          <td style="text-align:right; font-weight:700; color:${varVal >= 0 ? 'var(--success)' : 'var(--danger)'};">
            ${varVal >= 0 ? '+' : ''}${varVal.toLocaleString()}
          </td>
          <td><span style="font-size:0.82rem;">${r.reason || 'General Monthly Revision'}</span></td>
          <td><span class="pill pill-safe" style="font-size:0.72rem;">${r.revised_by || 'Sales Team'}</span></td>
        </tr>
      `;
    }).join('');
  } catch (e) {
    if (tbody) tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--danger); padding:1rem;">Failed to load revision history.</td></tr>';
  }
}

async function triggerRealignProduction() {
  const month = document.getElementById('prod-month-select')?.value || AppState.selectedMonth;
  const today = new Date().toISOString().split('T')[0];
  const effDate = prompt(`Enter effective date from which to re-align daily production plans with the revised forecast (YYYY-MM-DD):`, today);
  if (!effDate) return;

  try {
    showToast('Re-aligning production plans with revised forecast...', 'info');
    const res = await fetch('/api/production/realign-from-forecast', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ month, effective_date: effDate.trim() })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`✓ Successfully re-aligned ${data.plans_adjusted || data.realigned_count || 0} daily plans & store requisitions!`, 'success');
      loadProductionView();
    } else {
      showToast(data.error || 'Failed to re-align production plans', 'danger');
    }
  } catch (e) {
    showToast('Error connecting to realignment service', 'danger');
  }
}

function downloadForecastBlankTemplate() {
  const cat = AppState.selectedCategory;
  const month = document.getElementById('forecast-month-select')?.value || AppState.selectedMonth;
  window.location.href = `/api/forecast/template?category=${cat}&month=${month}`;
  showToast('Downloading blank Excel template...', 'success');
}

// Smart Production Plan Suggestion Modal
async function openSmartPlanModal() {
  const cat = AppState.selectedCategory;
  const month = AppState.selectedMonth;
  const container = document.getElementById('smart-plan-container');
  container.innerHTML = '<div style="text-align:center; padding:2rem;"><i class="fas fa-spinner fa-spin"></i> Calculating smart suggestions based on forecast trend and finished goods stock...</div>';
  showModal('modal-smart-plan');

  try {
    const res = await fetch(`/api/production/suggest?category=${cat}&month=${month}`);
    const data = await res.json();
    const suggestions = data.suggestions;

    container.innerHTML = `
      <div style="font-size:0.8rem; color:var(--text-muted); margin-bottom:1rem;">
        Formula: <strong>Recommended Plan = Forecast + Target Safety Buffer - Existing FG Stock</strong>. You can fine-tune any suggested quantity below before generating the plan.
      </div>
      <div class="table-responsive" style="max-height:350px; overflow-y:auto;">
        <table class="data-table">
          <thead>
            <tr>
              <th>SKU</th>
              <th>Forecast (Ctn)</th>
              <th>Est. Closing Stock</th>
              <th>Safety Buffer</th>
              <th>Suggested Plan (Ctn)</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            ${suggestions.map(s => `
              <tr>
                <td><strong>${s.sku_name}</strong> <span style="font-size:0.7rem; color:var(--text-dim);">(${s.sku_code})</span></td>
                <td><strong>${s.forecast_ctn}</strong></td>
                <td>${s.est_fg_stock}</td>
                <td>+${s.safety_buffer}</td>
                <td>
                  <input type="number" class="form-control smart-suggest-input" style="width:110px; font-weight:700;" data-sku-id="${s.sku_id}" value="${s.suggested_ctn}">
                </td>
                <td>
                  <button class="btn btn-sm btn-primary" onclick="quickCreatePlanFromSmart(${s.sku_id}, this)">
                    <i class="fas fa-check"></i> Create Plan
                  </button>
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `;
  } catch (e) {
    container.innerHTML = '<div style="color:var(--danger); padding:1rem;">Failed to load suggestions.</div>';
  }
}

async function quickCreatePlanFromSmart(skuId, btn) {
  const row = btn.closest('tr');
  const qty = parseFloat(row.querySelector('.smart-suggest-input').value) || 0;
  if (qty <= 0) {
    showToast('Please enter a valid quantity', 'warning');
    return;
  }

  try {
    const res = await fetch('/api/production/plans', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sku_id: skuId,
        planned_ctn: qty,
        month_year: AppState.selectedMonth,
        notes: 'Smart Suggester: Forecast Trend & Buffer Plan',
        auto_create_requisition: true
      })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Created Production Plan ${data.plan_no} & Requisition!`, 'success');
      btn.innerHTML = '<i class="fas fa-check-circle"></i> Created';
      btn.className = 'btn btn-sm btn-success';
      btn.disabled = true;
      loadDashboard();
    }
  } catch (e) {
    showToast('Failed to create plan', 'danger');
  }
}

// ----------------- 3. PRODUCTION PLANNING CONTROLLER -----------------
async function loadProductionView() {
  const cat = AppState.selectedCategory;
  const month = AppState.selectedMonth;
  try {
    const res = await fetch(`/api/production/plans?category=${cat}&month=${month}`);
    const plans = await res.json();
    renderProductionPlans(plans);
    applyRolePermissions();
  } catch (e) {
    console.error('Error loading production plans:', e);
  }
}

function renderProductionPlans(plans) {
  const tbody = document.getElementById('production-plans-table-body');
  if (!tbody) return;

  if (!plans || plans.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:2rem; color:var(--text-dim);">
      No production plans found for this category and month. Click "Create Production Plan" or "Smart Suggest Plan" to create one!
    </td></tr>`;
    return;
  }

  const isCarton = AppState.displayUOM === 'Carton';

  tbody.innerHTML = plans.map(p => {
    const ach = p.achievement_pct || 0;
    let badgeClass = 'pill-low';
    if (p.status === 'Completed') badgeClass = 'pill-safe';
    else if (p.status === 'In Production') badgeClass = 'pill-fast';
    else if (p.status === 'Planned') badgeClass = 'pill-critical';

    const plannedVal = isCarton ? p.planned_ctn : (p.planned_pcs || p.planned_ctn * p.ctn_size);
    const actualVal = isCarton ? p.actual_produced_ctn : (p.actual_produced_pcs || p.actual_produced_ctn * p.ctn_size);
    const openVal = isCarton ? (p.opening_stock_ctn || 0.0) : (p.opening_stock_pcs || (p.opening_stock_ctn || 0.0) * p.ctn_size);
    const uomLabel = isCarton ? 'Ctn' : (p.uom_default || 'Pcs');

    let prevDateLabel = 'Prev Day';
    let planDateLabel = p.plan_date || '';
    if (p.plan_date) {
      try {
        const pd = new Date(p.plan_date);
        planDateLabel = pd.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' });
        pd.setDate(pd.getDate() - 1);
        prevDateLabel = pd.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' });
      } catch (e) {}
    }

    return `
      <tr>
        <td><strong>${p.plan_no}</strong></td>
        <td><span style="font-weight:600; font-size:0.82rem;">📅 ${p.plan_date || '-'}</span></td>
        <td>
          <div style="font-weight:600;">${p.sku_name}</div>
          <div style="font-size:0.72rem; color:var(--text-dim);">${p.sku_code} • ${p.category}</div>
        </td>
        <td>
          <div style="font-weight:700; color:var(--text-main); font-size:0.95rem;">
            <i class="fas fa-warehouse" style="color:var(--primary); font-size:0.8rem; margin-right:3px;"></i>
            ${openVal.toLocaleString()} ${uomLabel}
          </div>
          <div style="font-size:0.68rem; color:var(--text-dim);" title="Poloxy Godown: ${p.godown_name || 'Godown'} [Group: Cut-Up-Part]">
            ${p.plan_date ? `Closing: ${prevDateLabel} / Open: ${planDateLabel}` : (p.godown_name ? p.godown_name.split(' ')[1] || p.godown_name : 'Poloxy In-Hand')}
          </div>
        </td>
        <td><strong>${plannedVal.toLocaleString()}</strong> ${uomLabel}</td>
        <td>
          <div style="font-weight:700; color:${ach >= 80 ? 'var(--success)' : 'var(--warning)'}; font-size:0.95rem;">${actualVal.toLocaleString()} ${uomLabel}</div>
          ${p.output_amount ? `<div style="font-size:0.68rem; color:var(--success); font-weight:600;">৳ ${p.output_amount.toLocaleString()}</div>` : ''}
        </td>
        <td>
          <span style="font-weight:600; color:${p.variance_ctn >= 0 ? 'var(--success)' : 'var(--danger)'};">
            ${p.variance_ctn >= 0 ? '+' : ''}${p.variance_ctn} Ctn
          </span>
        </td>
        <td style="width:110px;">
          <div style="display:flex; justify-content:space-between; font-size:0.72rem; margin-bottom:3px;">
            <span>${ach}%</span>
          </div>
          <div style="background:var(--bg-tertiary); height:6px; border-radius:4px; overflow:hidden;">
            <div style="background:${ach >= 100 ? 'var(--success)' : 'var(--primary)'}; height:100%; width:${Math.min(ach, 100)}%;"></div>
          </div>
        </td>
        <td><span class="pill ${badgeClass}">${p.status}</span></td>
        <td>
          ${p.requisition_id ? `
            <a href="#" onclick="viewRequisitionDetail(${p.requisition_id}); return false;" style="color:var(--primary); font-weight:600; text-decoration:none;">
              <i class="fas fa-file-invoice"></i> ${p.req_no}
            </a>
          ` : `<span style="color:var(--text-dim);">-</span>`}
        </td>
        <td>
          <div style="display:flex; gap:0.35rem;">
            ${canUserEdit('production') ? `
              <button class="btn btn-sm btn-outline" title="Update Output" onclick="openUpdateOutputModal(${p.id}, '${p.plan_no}', ${p.planned_ctn}, ${p.actual_produced_ctn})">
                <i class="fas fa-edit"></i> Output
              </button>
            ` : `<span class="pill pill-safe" style="font-size:0.7rem;"><i class="fas fa-lock"></i> Read Only</span>`}
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

// Poloxy ERP Smart Live Sync (Calendar Single Date & Range with Cache Bypass)
let syncDateMode = 'single';

function openPoloxySyncModal() {
  const cat = AppState.selectedCategory;
  const unitLabel = document.getElementById('poloxy-sync-unit-label');
  const wcLabel = document.getElementById('poloxy-sync-wc-label');
  
  const unitNames = {
    '05': 'Unit 05 (Dry Foods)',
    '02': 'Unit 02 (Frozen Foods)',
    '01': 'Unit 01 (Process Chicken)',
    'ALL': 'All Production Units'
  };
  const workCenters = {
    '05': 'Dry Food Packing (Bakery Puffed Sauce Chanachur & Lachsa) [Main Product]',
    '02': 'Frozen Finished Goods [Main Product]',
    '01': 'Process Chicken Plant [Main Product]',
    'ALL': 'Multi-Work Center'
  };
  const godowns = {
    '05': '05. Dry Food Factory Godown [Group: Cut-Up-Part]',
    '02': '02. Frozen Foods Factory Godown [Group: Cut-Up-Part]',
    '01': '01. Process Chicken Factory Godown [Group: Cut-Up-Part]',
    'ALL': 'Respective Factory Godowns [Group: Cut-Up-Part]'
  };

  if (unitLabel) unitLabel.textContent = unitNames[cat] || `Unit ${cat}`;
  if (wcLabel) wcLabel.innerHTML = `<strong>Output Work Center (Rule 8):</strong> ${workCenters[cat] || 'Paragon Plant'}`;
  const godownLabel = document.getElementById('poloxy-sync-godown-label');
  if (godownLabel) godownLabel.innerHTML = `<strong>Opening Stock Godown (Rule 9):</strong> ${godowns[cat] || '05. Dry Food Factory Godown [Group: Cut-Up-Part]'}`;

  // Default date setup
  const today = new Date().toISOString().split('T')[0];
  const sDate = document.getElementById('sync-single-date');
  const rFrom = document.getElementById('sync-range-from');
  const rTo = document.getElementById('sync-range-to');

  if (sDate) {
    sDate.value = today;
    updateRule9DateHelper();
  }
  if (rFrom) rFrom.value = `${AppState.selectedMonth}-01`;
  if (rTo) rTo.value = today;

  setSyncDateMode('single');

  const statusMsg = document.getElementById('sync-status-msg');
  if (statusMsg) {
    statusMsg.style.display = 'none';
    statusMsg.innerHTML = '';
  }

  const btn = document.getElementById('btn-submit-erp-sync');
  if (btn) {
    btn.disabled = false;
    btn.innerHTML = '<i class="fas fa-cloud-download-alt"></i> Pull Live ERP Data';
  }

  showModal('modal-poloxy-sync');
}

function updateRule9DateHelper() {
  const sDate = document.getElementById('sync-single-date');
  const cat = AppState.selectedCategory;
  const godownsShort = {
    '05': '05. Dry Food Factory Godown',
    '02': '02. Frozen Foods Factory Godown',
    '01': '01. Process Chicken Factory Godown',
    'ALL': 'Factory Godown'
  };
  const gName = godownsShort[cat] || 'Factory Godown';
  const textEl = document.getElementById('rule9-date-text');

  if (!sDate || !sDate.value) return;
  try {
    const parts = sDate.value.split('-');
    const td = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
    const pd = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
    pd.setDate(pd.getDate() - 1);
    const fmtTarget = td.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
    const fmtPrev = pd.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
    if (textEl) {
      textEl.innerHTML = `পরিকল্পিত তারিখ: <strong>${fmtTarget}</strong> এর জন্য <strong>${gName}</strong> [Cut-Up-Part] থেকে <strong>${fmtPrev}</strong>-এর <strong>Closing Stock</strong> (অথবা <strong>${fmtTarget}</strong>-এর <strong>Opening Stock</strong>) ইন-হ্যান্ড ব্যালেন্স হিসেবে নেওয়া হবে।`;
    }
  } catch (e) {}
}

function setSyncDateMode(mode) {
  syncDateMode = mode;
  const btnSingle = document.getElementById('btn-sync-mode-single');
  const btnRange = document.getElementById('btn-sync-mode-range');
  const boxSingle = document.getElementById('sync-container-single');
  const boxRange = document.getElementById('sync-container-range');

  if (btnSingle && btnRange) {
    btnSingle.classList.toggle('active', mode === 'single');
    btnRange.classList.toggle('active', mode === 'range');
  }

  if (boxSingle) boxSingle.style.display = (mode === 'single') ? 'block' : 'none';
  if (boxRange) boxRange.style.display = (mode === 'range') ? 'block' : 'none';
  if (mode === 'single') updateRule9DateHelper();
}

function setSyncQuickDate(type) {
  const sDate = document.getElementById('sync-single-date');
  if (!sDate) return;
  const d = new Date();
  if (type === 'today') {
    sDate.value = d.toISOString().split('T')[0];
  } else if (type === 'yesterday') {
    d.setDate(d.getDate() - 1);
    sDate.value = d.toISOString().split('T')[0];
  }
  updateRule9DateHelper();
}

function setSyncQuickRange(type) {
  const rFrom = document.getElementById('sync-range-from');
  const rTo = document.getElementById('sync-range-to');
  if (!rFrom || !rTo) return;
  
  const today = new Date();
  rTo.value = today.toISOString().split('T')[0];

  if (type === 'week') {
    const past = new Date();
    past.setDate(past.getDate() - 7);
    rFrom.value = past.toISOString().split('T')[0];
  } else if (type === 'mtd') {
    rFrom.value = `${AppState.selectedMonth}-01`;
  } else if (type === 'full_month') {
    rFrom.value = `${AppState.selectedMonth}-01`;
    // Last day of selected month
    const parts = AppState.selectedMonth.split('-');
    const lastDay = new Date(parseInt(parts[0]), parseInt(parts[1]), 0).getDate();
    rTo.value = `${AppState.selectedMonth}-${lastDay}`;
  }
}

async function submitPoloxySync() {
  const btn = document.getElementById('btn-submit-erp-sync');
  const statusMsg = document.getElementById('sync-status-msg');

  let payload = {
    category_code: AppState.selectedCategory,
    month: AppState.selectedMonth,
    mode: syncDateMode,
    action: (typeof syncActionType !== 'undefined' ? syncActionType : 'both'),
    _nocache: Date.now() // Strict cache-busting token
  };

  if (syncDateMode === 'single') {
    const sDate = document.getElementById('sync-single-date').value;
    if (!sDate) {
      showToast('Please select a production date.', 'warning');
      return;
    }
    payload.date = sDate;
  } else {
    const from = document.getElementById('sync-range-from').value;
    const to = document.getElementById('sync-range-to').value;
    if (!from || !to) {
      showToast('Please select both From and To dates for the range.', 'warning');
      return;
    }
    if (from > to) {
      showToast('From date cannot be after To date.', 'warning');
      return;
    }
    payload.date_from = from;
    payload.date_to = to;
  }

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Connecting to Poloxy ERP...';
  }

  if (statusMsg) {
    statusMsg.style.display = 'block';
    statusMsg.style.color = 'var(--text-muted)';
    statusMsg.innerHTML = '<i class="fas fa-sync-alt fa-spin" style="margin-right:5px; color:var(--primary);"></i> Smart Engine contacting Poloxy ERP live server... Bypassing local cache.';
  }

  try {
    const res = await fetch('/api/erp/sync', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache'
      },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    
    if (data.success) {
      if (statusMsg) {
        statusMsg.style.color = 'var(--success)';
        statusMsg.innerHTML = `
          <div style="background:rgba(16,185,129,0.1); border:1px solid rgba(16,185,129,0.3); border-radius:6px; padding:0.5rem 0.75rem; margin-top:0.35rem;">
            <strong><i class="fas fa-check-circle"></i> Live ERP Pull Complete!</strong><br>
            <span>Date: <strong>${data.date_range}</strong></span> • 
            <span>Plans Updated: <strong>${data.plans_updated}</strong></span> • 
            <span>Output: <strong>${(data.total_produced_cartons || 0).toLocaleString()} Ctns</strong></span><br>
            <small style="color:var(--text-dim); font-size:0.72rem;">Live Timestamp: ${data.live_timestamp} (Zero cache used)</small>
          </div>
        `;
      }
      showToast(`⚡ Pulled live ERP data for ${data.date_range} (${data.plans_updated} plans updated)`, 'success');
      loadProductionView();
      loadDashboard();
      setTimeout(() => {
        hideModal('modal-poloxy-sync');
      }, 1600);
    } else {
      if (statusMsg) {
        statusMsg.style.color = 'var(--danger)';
        statusMsg.innerHTML = `<i class="fas fa-exclamation-circle"></i> ${data.error || 'Failed to pull data from ERP'}`;
      }
      showToast('ERP Sync failed', 'danger');
    }
  } catch (e) {
    if (statusMsg) {
      statusMsg.style.color = 'var(--danger)';
      statusMsg.innerHTML = '<i class="fas fa-exclamation-circle"></i> Connection error while reaching ERP server.';
    }
    showToast('Network error during ERP sync', 'danger');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<i class="fas fa-cloud-download-alt"></i> Pull Live ERP Data';
    }
  }
}

// Legacy alias for backwards compatibility
function syncFromPoloxyERP() {
  openPoloxySyncModal();
}

// ----------------- 4. STORE & STOCK (TWO SEPARATE TABS) -----------------
function switchStoreSubTab(tabName) {
  AppState.storeSubTab = tabName;
  document.querySelectorAll('.store-tab-link').forEach(l => {
    l.classList.toggle('active', l.getAttribute('data-subtab') === tabName);
  });
  document.getElementById('store-tab-inventory').style.display = (tabName === 'inventory') ? 'block' : 'none';
  document.getElementById('store-tab-requisitions').style.display = (tabName === 'requisitions') ? 'block' : 'none';
  document.getElementById('store-tab-mrr').style.display = (tabName === 'mrr') ? 'block' : 'none';
  document.getElementById('store-tab-wastage').style.display = (tabName === 'wastage') ? 'block' : 'none';

  if (tabName === 'inventory') loadStoreStock();
  else if (tabName === 'requisitions') loadIncomingRequisitions();
  else if (tabName === 'mrr') loadMRREntries();
  else if (tabName === 'wastage') loadWastageEntries();
}

async function loadStoreView() {
  switchStoreSubTab(AppState.storeSubTab);
}

async function loadStoreStock() {
  const q = document.getElementById('store-search-input')?.value || '';
  const filter = AppState.storeFilter;
  const cat = AppState.selectedCategory;
  let url = `/api/store/stock?category=${cat}&q=${encodeURIComponent(q)}`;

  if (filter === 'RM' || filter === 'PM') url += `&type=${filter}`;
  if (filter === 'critical') url += `&status=critical`;
  if (filter === 'low') url += `&status=low`;
  if (filter === 'safe') url += `&status=safe`;
  if (filter === 'fast' || filter === 'slow' || filter === 'non_moving') url += `&fsn=${filter}`;

  try {
    const res = await fetch(url);
    const items = await res.json();
    renderStoreTable(items);
    applyRolePermissions();
  } catch (e) {
    console.error('Error loading store stock:', e);
  }
}

function setStoreFilter(filterType, btn) {
  AppState.storeFilter = filterType || 'all';
  document.querySelectorAll('#store-tabs .tab-btn').forEach(b => {
    b.classList.remove('active');
    const oc = b.getAttribute('onclick') || '';
    if (oc.includes(`'${AppState.storeFilter}'`)) {
      b.classList.add('active');
    }
  });
  if (btn) btn.classList.add('active');
  const searchInp = document.getElementById('store-search-input');
  if (searchInp && AppState.storeFilter === 'all') {
    searchInp.value = '';
  }
  loadStoreStock();
}

function renderStoreTable(items) {
  const tbody = document.getElementById('store-stock-table-body');
  if (!tbody) return;

  if (!items || items.length === 0) {
    tbody.innerHTML = `<tr><td colspan="10" style="text-align:center; padding:2rem; color:var(--text-dim);">No materials found matching criteria.</td></tr>`;
    return;
  }

  tbody.innerHTML = items.map(m => {
    let pillClass = 'pill-safe';
    if (m.stock_status === 'Critical') pillClass = 'pill-critical';
    else if (m.stock_status === 'Low') pillClass = 'pill-low';

    const floorStock = m.floor_stock || 0.0;
    const totalPlant = m.total_plant_stock || (m.current_stock + floorStock);

    return `
      <tr>
        <td><strong>${m.item_code}</strong></td>
        <td>
          <div style="font-weight:600;">${m.name}</div>
          <div style="font-size:0.72rem; color:var(--text-dim);">${m.supplier_name || 'Standard Supplier'} • Lead: ${m.lead_time_days || 7}d</div>
        </td>
        <td><span class="pill ${m.type === 'RM' ? 'pill-safe' : 'pill-fast'}">${m.type}</span></td>
        <td>
          <span class="pill ${m.fsn_status === 'Fast' ? 'pill-fsn-fast' : (m.fsn_status === 'Slow' ? 'pill-fsn-slow' : 'pill-fsn-nonmoving')}">
            ${m.fsn_status === 'Fast' ? '⚡ Fast' : (m.fsn_status === 'Slow' ? '🐢 Slow' : '💤 Non-Moving')}
          </span>
        </td>
        <td>
          <a href="javascript:void(0)" class="aging-stock-link" onclick="openMaterialAgingModal(${m.id})" style="font-weight:700; color:${m.stock_status === 'Critical' ? 'var(--danger)' : 'var(--primary)'}; font-size:0.95rem; text-decoration:underline; cursor:pointer;" title="Click to view Batch Receiving Dates & Shelf-Life Aging Breakdown">
            ${m.current_stock.toLocaleString()}
          </a> ${m.unit}
          <div style="font-size:0.68rem; color:var(--text-dim);"><i class="fas fa-history"></i> Click for Aging</div>
        </td>
        <td>
          <span style="font-weight:600; color:var(--cyan); font-size:0.92rem;">${floorStock.toLocaleString()}</span> ${m.unit}
        </td>
        <td>
          <strong style="color:var(--text-main); font-size:0.95rem;">${totalPlant.toLocaleString()}</strong> ${m.unit}
        </td>
        <td>
          <span style="font-weight:600;">${m.safety_stock.toLocaleString()}</span> ${m.unit}
          ${canUserEdit('store') ? `
            <button class="btn btn-sm btn-outline" style="padding:1px 5px; font-size:0.65rem; margin-left:4px;" title="Edit Safety Stock" onclick="openEditSafetyStockModal(${m.id}, '${m.name.replace(/'/g, "\\'")}', ${m.safety_stock})">
              <i class="fas fa-pen"></i>
            </button>
          ` : ''}
        </td>
        <td>
          ${m.in_transit > 0 
            ? `<span class="pill pill-fast"><i class="fas fa-truck"></i> ${m.in_transit.toLocaleString()} ${m.unit}</span>` 
            : `<span style="color:var(--text-dim);">-</span>`}
        </td>
        <td><span class="pill ${pillClass}">${m.stock_status}</span></td>
        <td><strong>৳ ${m.stock_value.toLocaleString()}</strong></td>
        <td>
          <div style="display:flex; gap:0.35rem;">
            ${canUserEdit('store') ? `
              <button class="btn btn-sm btn-outline" title="Adjust Stock" onclick="openAdjustStockModal(${m.id}, '${m.name.replace(/'/g, "\\'")}', ${m.current_stock})">
                <i class="fas fa-sliders-h"></i>
              </button>
              <button class="btn btn-sm btn-primary" title="Raise Purchase Requisition" onclick="openRaisePurchaseModal(${m.id}, '${m.name.replace(/'/g, "\\'")}', ${Math.max(m.safety_stock * 2, m.safety_stock - m.current_stock)}, ${m.unit_price}, '${m.supplier_name}')">
                <i class="fas fa-cart-plus"></i>
              </button>
            ` : `<span class="pill pill-safe" style="font-size:0.7rem;"><i class="fas fa-lock"></i> View Only</span>`}
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

// Tab 2: Incoming Production Requisitions (Matches Screenshot 2!)
async function loadIncomingRequisitions() {
  const cat = AppState.selectedCategory;
  try {
    const res = await fetch(`/api/requisitions/production?category=${cat}`);
    const reqs = await res.json();
    const container = document.getElementById('store-incoming-reqs-list');
    if (!container) return;

    if (!reqs || reqs.length === 0) {
      container.innerHTML = '<div style="color:var(--text-dim); text-align:center; padding:2rem;">No material requisitions found for this unit.</div>';
      return;
    }

    container.innerHTML = reqs.map(r => {
      const isIssued = r.status === 'Issued';
      const pillClass = isIssued ? 'pill-safe' : (r.status === 'Partially Issued' ? 'pill-low' : 'pill-critical');

      return `
        <div style="background:var(--bg-tertiary); border:1px solid var(--card-border); border-radius:10px; padding:1.15rem; margin-bottom:0.85rem; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:0.75rem;">
          <div>
            <div style="display:flex; align-items:center; gap:0.6rem;">
              <strong style="font-size:1.05rem;">${r.req_no}</strong>
              <span class="pill ${pillClass}">${r.status}</span>
            </div>
            <div style="font-size:0.85rem; margin-top:0.35rem;">
              For: <strong>${r.sku_name || 'Production Batch'}</strong> (${r.planned_ctn || 0} Cartons)
            </div>
            <div style="font-size:0.74rem; color:var(--text-dim); margin-top:0.25rem;">
              Requested: ${r.req_date} by ${r.requested_by} • Items: ${r.issued_items}/${r.total_items} issued
            </div>
          </div>
          <div style="display:flex; align-items:center; gap:0.6rem;">
            <button class="btn btn-sm btn-outline" onclick="viewRequisitionDetail(${r.id})">
              <i class="fas fa-list"></i> View Details
            </button>
            ${(!isIssued && canUserEdit('store')) ? `
              <button class="btn btn-sm btn-success" onclick="issueAllRequisitionMaterials(${r.id})">
                <i class="fas fa-box-open"></i> Issue Materials to Production
              </button>
            ` : (!isIssued ? `
              <span class="pill pill-safe" style="font-size:0.75rem;"><i class="fas fa-lock"></i> Store Issue Only</span>
            ` : `
              <span style="font-size:0.8rem; color:var(--success); font-weight:700;"><i class="fas fa-check-circle"></i> Issued</span>
            `)}
          </div>
        </div>
      `;
    }).join('');
  } catch (e) {
    console.error('Error loading incoming requisitions:', e);
  }
}

// Tab 3: MRR Inward Register (Material Receive Reports)
async function loadMRREntries() {
  const cat = AppState.selectedCategory;
  try {
    const res = await fetch(`/api/store/mrr?category=${cat}`);
    const rows = await res.json();
    const tbody = document.getElementById('store-mrr-tbody');
    if (!tbody) return;

    if (!rows || rows.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; padding:2rem; color:var(--text-dim);">No MRR entries found. Click "+ New MRR Stock Entry" to record inward goods!</td></tr>';
      return;
    }

    tbody.innerHTML = rows.map(r => `
      <tr>
        <td><strong>${r.mrr_no}</strong></td>
        <td>${r.mrr_date}</td>
        <td>
          <div style="font-weight:600;">${r.supplier_name}</div>
          <div style="font-size:0.7rem; color:var(--text-dim);">Chalan: ${r.chalan_no || '-'}</div>
        </td>
        <td><strong>${r.material_name}</strong> <span style="font-size:0.7rem; color:var(--text-dim);">(${r.item_code})</span></td>
        <td><strong>${r.received_qty.toLocaleString()}</strong> ${r.unit}</td>
        <td>৳ ${r.unit_price}</td>
        <td><strong>৳ ${r.total_amount.toLocaleString()}</strong></td>
        <td><span style="font-size:0.75rem; color:var(--text-dim);">${r.created_by}</span></td>
      </tr>
    `).join('');
  } catch (e) {
    console.error('Error loading MRR entries:', e);
  }
}

function openCreateMRRModal() {
  const select = document.getElementById('mrr-material-select');
  select.innerHTML = '<option value="">-- Choose Material --</option>' +
    AppState.materials.map(m => `<option value="${m.id}" data-price="${m.unit_price}" data-supplier="${m.supplier_name}">[${m.type}] ${m.name} (${m.unit})</option>`).join('');
  document.getElementById('mrr-date').value = new Date().toISOString().split('T')[0];
  showModal('modal-create-mrr');
}

async function submitCreateMRR() {
  const select = document.getElementById('mrr-material-select');
  const matId = select.value;
  const qty = parseFloat(document.getElementById('mrr-qty').value) || 0;
  const price = parseFloat(document.getElementById('mrr-price').value) || 0;
  const supplier = document.getElementById('mrr-supplier').value;
  const chalan = document.getElementById('mrr-chalan').value;
  const vehicle = document.getElementById('mrr-vehicle').value;
  const dateVal = document.getElementById('mrr-date').value;

  if (!matId || qty <= 0) {
    showToast('Valid material and quantity are required', 'warning');
    return;
  }

  try {
    const res = await fetch('/api/store/mrr', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        material_id: parseInt(matId),
        received_qty: qty,
        unit_price: price,
        supplier_name: supplier,
        chalan_no: chalan,
        vehicle_no: vehicle,
        mrr_date: dateVal
      })
    });
    const data = await res.json();
    if (data.success) {
      hideModal('modal-create-mrr');
      showToast(`MRR ${data.mrr_no} saved! New stock: ${data.new_stock}`, 'success');
      loadMRREntries();
      loadStoreStock();
      loadDashboard();
    }
  } catch (e) {
    showToast('Failed to save MRR entry', 'danger');
  }
}

// Tab 4: Wastage / Scrap Register
async function loadWastageEntries() {
  const cat = AppState.selectedCategory;
  try {
    const res = await fetch(`/api/wastage?category=${cat}`);
    const rows = await res.json();
    const tbody = document.getElementById('store-wastage-tbody');
    if (!tbody) return;

    if (!rows || rows.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding:2rem; color:var(--text-dim);">No wastage entries recorded. Click "+ Record Wastage Entry" to log scrap.</td></tr>';
      return;
    }

    tbody.innerHTML = rows.map(w => `
      <tr>
        <td>${w.date}</td>
        <td><strong>${w.item_name}</strong> <span style="font-size:0.7rem; color:var(--text-dim);">(${w.item_code})</span></td>
        <td><span class="pill ${w.item_type === 'FG' ? 'pill-safe' : 'pill-fast'}">${w.item_type}</span></td>
        <td><strong>${w.qty.toLocaleString()}</strong> ${w.unit}</td>
        <td><span class="pill pill-critical">${w.reason}</span></td>
        <td><strong>৳ ${w.estimated_cost.toLocaleString()}</strong></td>
        <td>${w.remarks || '-'}</td>
      </tr>
    `).join('');
  } catch (e) {
    console.error('Error loading wastage entries:', e);
  }
}

function openCreateWastageModal() {
  const select = document.getElementById('wastage-item-select');
  select.innerHTML = '<option value="">-- Choose RM or PM --</option>' +
    AppState.materials.map(m => `<option value="${m.id}" data-type="${m.type}">[${m.type}] ${m.name}</option>`).join('');
  document.getElementById('wastage-date').value = new Date().toISOString().split('T')[0];
  showModal('modal-create-wastage');
}

async function submitCreateWastage() {
  const select = document.getElementById('wastage-item-select');
  const itemId = select.value;
  const itemType = select.options[select.selectedIndex]?.getAttribute('data-type') || 'RM';
  const qty = parseFloat(document.getElementById('wastage-qty').value) || 0;
  const reason = document.getElementById('wastage-reason').value;
  const batch = document.getElementById('wastage-batch').value;
  const dateVal = document.getElementById('wastage-date').value;
  const remarks = document.getElementById('wastage-remarks').value;

  if (!itemId || qty <= 0) {
    showToast('Valid item and quantity are required', 'warning');
    return;
  }

  try {
    const res = await fetch('/api/wastage', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        item_id: parseInt(itemId),
        item_type: itemType,
        qty,
        reason,
        batch_no: batch,
        date: dateVal,
        remarks,
        category_code: AppState.selectedCategory
      })
    });
    const data = await res.json();
    if (data.success) {
      hideModal('modal-create-wastage');
      showToast('Wastage entry recorded and stock adjusted!', 'success');
      loadWastageEntries();
      loadStoreStock();
      loadDashboard();
    }
  } catch (e) {
    showToast('Failed to record wastage', 'danger');
  }
}

// ----------------- 5. SUPER ADMIN: MASTER & BOM & USERS & ERP -----------------
async function loadMasterBOMView() {
  loadAdminSKUs();
  loadAdminMaterials();
  loadAdminUsers();
  loadERPSettings();
  loadTursoStatusUI();
}

function setAdminTab(tab) {
  AppState.adminSubTab = tab;
  document.querySelectorAll('#admin-tabs .tab-btn').forEach(b => {
    b.classList.toggle('active', b.getAttribute('data-tab') === tab);
  });
  document.querySelectorAll('.admin-tab-content').forEach(c => c.style.display = 'none');
  const target = document.getElementById(`admin-tab-${tab}`);
  if (target) target.style.display = 'block';

  if (tab === 'skus') loadAdminSKUs();
  else if (tab === 'materials') loadAdminMaterials();
  else if (tab === 'users') loadAdminUsers();
  else if (tab === 'erp') loadERPSettings();
  else if (tab === 'cloud') loadTursoStatusUI();
}

async function loadAdminSKUs() {
  try {
    const res = await fetch('/api/skus');
    const skus = await res.json();
    AppState.skus = skus;
    const tbody = document.getElementById('admin-sku-table-body');
    if (!tbody) return;

    tbody.innerHTML = skus.map(s => `
      <tr>
        <td><strong>${s.sku_code}</strong></td>
        <td><strong>${s.name}</strong></td>
        <td><span class="pill pill-safe">Unit ${s.category_code}</span></td>
        <td>${s.pack_size || '-'}</td>
        <td>
          <input type="number" class="form-control" style="width:85px; font-weight:700;" value="${s.ctn_size}"
                 onchange="updateSKUConversion(${s.id}, this.value)">
          <span style="font-size:0.7rem; color:var(--text-dim);">${s.uom_default}/Ctn</span>
        </td>
        <td><span class="pill pill-safe">${s.status}</span></td>
        <td>
          <button class="btn btn-sm btn-primary" onclick="openBOMBuilder(${s.id}, '${s.name.replace(/'/g, "\\'")}')">
            <i class="fas fa-sitemap"></i> Edit BOM
          </button>
        </td>
      </tr>
    `).join('');

    const bomSelect = document.getElementById('bom-active-sku-select');
    if (bomSelect) {
      bomSelect.innerHTML = '<option value="">-- Choose Finished Good to Edit BOM --</option>' +
        skus.map(s => `<option value="${s.id}">[${s.category_code}] ${s.sku_code} - ${s.name}</option>`).join('');
    }
  } catch (e) {
    console.error('Error loading admin SKUs:', e);
  }
}

async function updateSKUConversion(skuId, ctnSize) {
  try {
    const res = await fetch(`/api/skus/${skuId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ctn_size: parseInt(ctnSize) || 12 })
    });
    const data = await res.json();
    if (data.success) {
      showToast('Conversion rate updated!', 'success');
    }
  } catch (e) {
    showToast('Failed to update conversion rate', 'danger');
  }
}

async function loadAdminMaterials() {
  try {
    const res = await fetch('/api/materials');
    const mats = await res.json();
    AppState.materials = mats;
    const tbody = document.getElementById('admin-mat-table-body');
    if (!tbody) return;

    tbody.innerHTML = mats.map(m => `
      <tr>
        <td><strong>${m.item_code}</strong></td>
        <td><strong>${m.name}</strong></td>
        <td><span class="pill ${m.type === 'RM' ? 'pill-safe' : 'pill-fast'}">${m.type}</span></td>
        <td>Unit ${m.category_code}</td>
        <td>${m.unit}</td>
        <td>৳ ${m.unit_price}</td>
        <td>${m.safety_stock.toLocaleString()}</td>
        <td>${m.supplier_name || '-'}</td>
        <td>
          <button class="btn btn-sm btn-outline" onclick="openEditMaterialModal(${m.id})">
            <i class="fas fa-pen"></i>
          </button>
        </td>
      </tr>
    `).join('');
  } catch (e) {
    console.error('Error loading admin materials:', e);
  }
}

// User Management (RBAC)
async function loadAdminUsers() {
  if (!isSuperAdminUser()) {
    showToast('Only Super Admin can access User Management', 'warning');
    switchView('dashboard');
    return;
  }
  try {
    const res = await fetch('/api/users');
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      showToast(err.error || 'Access denied to user management', 'warning');
      return;
    }
    const users = await res.json();
    if (!Array.isArray(users)) return;
    
    const countPill = document.getElementById('users-count-pill');
    if (countPill) countPill.textContent = `${users.length} Registered Users`;

    const renderRow = (u) => `
      <tr>
        <td><strong><code style="color:var(--cyan); font-weight:700;">${u.username}</code></strong></td>
        <td><strong>${u.name}</strong></td>
        <td><span class="pill ${u.role === 'Super Admin' ? 'pill-critical' : u.role === 'Admin' ? 'pill-fast' : 'pill-safe'}">${u.role}</span></td>
        <td>${u.department}</td>
        <td>
          <span class="pill ${u.allowed_category === 'ALL' ? 'pill-safe' : 'pill-warning'}">
            ${u.allowed_category === 'ALL' ? '🌐 All Units (Full Access)' : '🔒 Unit ' + u.allowed_category + ' Only'}
          </span>
        </td>
        <td>
          <span style="font-family:monospace; background:var(--bg-tertiary); padding:3px 7px; border-radius:4px; font-size:0.8rem; border:1px solid var(--border-color);">
            ${u.password || '••••••'}
          </span>
        </td>
        <td>${u.is_active ? '<span class="pill pill-safe">Active</span>' : '<span class="pill pill-critical">Inactive</span>'}</td>
        <td>
          <button class="btn btn-sm btn-outline" title="Edit User Credentials" onclick="openEditUserModal(${u.id}, '${u.username}', '${u.name.replace(/'/g, "\\'")}', '${u.role}', '${u.department}', '${u.allowed_category}')">
            <i class="fas fa-edit"></i> Edit
          </button>
          ${u.id !== 1 ? `
            <button class="btn btn-sm btn-danger" title="Delete User" onclick="deleteUser(${u.id})">
              <i class="fas fa-trash"></i>
            </button>
          ` : ''}
        </td>
      </tr>
    `;

    const tbody1 = document.getElementById('users-table-body');
    if (tbody1) tbody1.innerHTML = users.map(renderRow).join('');

    const tbody2 = document.getElementById('admin-users-tbody');
    if (tbody2) tbody2.innerHTML = users.map(renderRow).join('');
  } catch (e) {
    console.error('Error loading admin users:', e);
  }
}

function openCreateUserModal() {
  document.getElementById('form-create-user')?.reset();
  showModal('modal-create-user');
}

async function submitCreateUser() {
  const username = document.getElementById('new-user-username').value.trim();
  const name = document.getElementById('new-user-name').value.trim();
  const password = document.getElementById('new-user-pass').value.trim();
  const role = document.getElementById('new-user-role').value;
  const dept = document.getElementById('new-user-dept').value;
  const allowed = document.getElementById('new-user-allowed-cat').value;

  if (!username || !password || !name) {
    showToast('Username, Name, and Password are required', 'warning');
    return;
  }

  try {
    const res = await fetch('/api/users', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, name, password, role, department: dept, allowed_category: allowed })
    });
    const data = await res.json();
    if (data.success) {
      hideModal('modal-create-user');
      showToast(`User ${username} created!`, 'success');
      loadAdminUsers();
    }
  } catch (e) {
    showToast('Failed to create user', 'danger');
  }
}

function openEditUserModal(id, username, name, role, dept, allowed) {
  document.getElementById('edit-user-id').value = id;
  document.getElementById('edit-user-name').value = name;
  document.getElementById('edit-user-role').value = role;
  document.getElementById('edit-user-dept').value = dept;
  document.getElementById('edit-user-allowed-cat').value = allowed;
  document.getElementById('edit-user-pass').value = '';
  showModal('modal-edit-user');
}

async function submitEditUser() {
  const id = document.getElementById('edit-user-id').value;
  const name = document.getElementById('edit-user-name').value.trim();
  const role = document.getElementById('edit-user-role').value;
  const dept = document.getElementById('edit-user-dept').value;
  const allowed = document.getElementById('edit-user-allowed-cat').value;
  const pass = document.getElementById('edit-user-pass').value.trim();

  try {
    const res = await fetch(`/api/users/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, role, department: dept, allowed_category: allowed, password: pass })
    });
    const data = await res.json();
    if (data.success) {
      hideModal('modal-edit-user');
      showToast('User updated successfully!', 'success');
      loadAdminUsers();
    }
  } catch (e) {
    showToast('Failed to update user', 'danger');
  }
}

async function deleteUser(id) {
  if (!confirm('Are you sure you want to delete this user?')) return;
  try {
    const res = await fetch(`/api/users/${id}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.success) {
      showToast('User deleted', 'success');
      loadAdminUsers();
    }
  } catch (e) {
    showToast('Failed to delete user', 'danger');
  }
}

// Poloxy ERP Settings Tab
async function loadERPSettings() {
  try {
    const res = await fetch('/api/erp/settings');
    const s = await res.json();
    if (document.getElementById('erp-cfg-url')) document.getElementById('erp-cfg-url').value = s.poloxy_url || 'http://erp.paragon.com.bd';
    if (document.getElementById('erp-cfg-user')) document.getElementById('erp-cfg-user').value = s.poloxy_user || 'a0162e0019';
    if (document.getElementById('erp-cfg-pass')) document.getElementById('erp-cfg-pass').value = s.poloxy_pass || 'pal.123456';
    if (document.getElementById('erp-cfg-dry-wc')) document.getElementById('erp-cfg-dry-wc').value = s.dry_food_work_center || 'Dry Food Packing(Bakery Puffed Sauce Chanachur & Lachsa)';
    if (document.getElementById('erp-cfg-frozen-wc')) document.getElementById('erp-cfg-frozen-wc').value = s.frozen_food_work_center || 'Frozen Finished Goods';
  } catch (e) {
    console.error('Error loading ERP settings:', e);
  }
}

async function saveERPSettings() {
  const url = document.getElementById('erp-cfg-url').value.trim();
  const user = document.getElementById('erp-cfg-user').value.trim();
  const pass = document.getElementById('erp-cfg-pass').value.trim();
  const dryWc = document.getElementById('erp-cfg-dry-wc').value.trim();
  const fzWc = document.getElementById('erp-cfg-frozen-wc').value.trim();

  try {
    const res = await fetch('/api/erp/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        poloxy_url: url,
        poloxy_user: user,
        poloxy_pass: pass,
        dry_food_work_center: dryWc,
        frozen_food_work_center: fzWc
      })
    });
    const data = await res.json();
    if (data.success) {
      showToast('Poloxy ERP configuration saved successfully!', 'success');
    }
  } catch (e) {
    showToast('Failed to save ERP settings', 'danger');
  }
}

// ----------------- TURSO CLOUD DATABASE UI HANDLERS -----------------
async function loadTursoStatusUI() {
  const badge = document.getElementById('turso-status-badge');
  const urlInp = document.getElementById('turso-cfg-url');
  const resBox = document.getElementById('turso-test-result');

  try {
    const res = await fetch('/api/cloud/turso/status');
    const data = await res.json();
    if (data.is_configured && data.is_connected) {
      if (badge) {
        badge.className = 'pill pill-safe';
        badge.innerHTML = '<i class="fas fa-check-circle"></i> Turso Cloud Active';
      }
      if (urlInp && !urlInp.value) urlInp.value = data.database_url || '';
    } else if (data.is_configured) {
      if (badge) {
        badge.className = 'pill pill-critical';
        badge.innerHTML = '<i class="fas fa-exclamation-triangle"></i> Turso Configured (Offline)';
      }
      if (urlInp && !urlInp.value) urlInp.value = data.database_url || '';
    } else {
      if (badge) {
        badge.className = 'pill pill-low';
        badge.innerHTML = '<i class="fas fa-circle"></i> Local SQLite Mode';
      }
    }
  } catch (e) {}
}

async function testTursoConnectionUI() {
  const url = document.getElementById('turso-cfg-url')?.value.trim();
  const token = document.getElementById('turso-cfg-token')?.value.trim();
  const resBox = document.getElementById('turso-test-result');
  const btn = document.getElementById('btn-test-turso');

  if (!url || !token) {
    showToast('Please enter both Turso Database URL and Auth Token', 'warning');
    return;
  }

  if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Connecting to Turso Cloud...'; }
  if (resBox) { resBox.style.display = 'block'; resBox.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Testing live connection to Turso Cloud database...'; resBox.style.color = 'var(--text-muted)'; }

  try {
    const res = await fetch('/api/cloud/turso/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, token })
    });
    const data = await res.json();
    if (data.success) {
      if (resBox) {
        resBox.innerHTML = `
          <div class="alert alert-success" style="padding:0.75rem 1rem; border-radius:8px;">
            <strong><i class="fas fa-check-circle"></i> Connection Successful!</strong><br>
            <span>Remote SQLite Version: <strong>${data.sqlite_version}</strong></span> • 
            <span>Existing Tables: <strong>${data.existing_tables}</strong></span><br>
            <small style="color:var(--text-dim);">Server Time: ${data.server_time}</small>
          </div>
        `;
      }
      showToast('Connected to Turso Cloud successfully!', 'success');
    } else {
      if (resBox) {
        resBox.innerHTML = `
          <div class="alert alert-danger" style="padding:0.75rem 1rem; border-radius:8px;">
            <strong><i class="fas fa-exclamation-circle"></i> Connection Failed!</strong><br>
            <span>${data.error}</span>
          </div>
        `;
      }
      showToast('Turso connection failed', 'danger');
    }
  } catch (e) {
    if (resBox) resBox.innerHTML = '<div class="alert alert-danger">Network error connecting to server.</div>';
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-plug"></i> Test Cloud Connection'; }
  }
}

async function saveTursoConfigUI() {
  const url = document.getElementById('turso-cfg-url')?.value.trim();
  const token = document.getElementById('turso-cfg-token')?.value.trim();
  const btn = document.getElementById('btn-save-turso');

  if (!url || !token) {
    showToast('Please enter both Turso Database URL and Auth Token', 'warning');
    return;
  }

  if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...'; }

  try {
    const res = await fetch('/api/cloud/turso/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, token })
    });
    const data = await res.json();
    if (data.success) {
      showToast('Turso Cloud Database activated! All users will now sync to cloud.', 'success');
      loadTursoStatusUI();
    } else {
      showToast(data.error || 'Failed to activate Turso config', 'danger');
    }
  } catch (e) {
    showToast('Failed to save cloud config', 'danger');
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-save"></i> Save & Activate Cloud DB'; }
  }
}

async function migrateLocalToTursoUI() {
  const url = document.getElementById('turso-cfg-url')?.value.trim();
  const token = document.getElementById('turso-cfg-token')?.value.trim();
  const btn = document.getElementById('btn-migrate-turso');
  const resBox = document.getElementById('turso-test-result');

  if (!confirm('Are you sure you want to copy all local SKUs, Materials, BOM, Plans, and Users to Turso Cloud?')) {
    return;
  }

  if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Migrating Data to Cloud...'; }
  if (resBox) { resBox.style.display = 'block'; resBox.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Migrating local schema & data to Turso Cloud... Please wait.'; resBox.style.color = 'var(--text-muted)'; }

  try {
    const res = await fetch('/api/cloud/turso/migrate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, token })
    });
    const data = await res.json();
    if (data.success) {
      if (resBox) {
        resBox.innerHTML = `
          <div class="alert alert-success" style="padding:0.75rem 1rem; border-radius:8px;">
            <strong><i class="fas fa-check-circle"></i> Migration Completed!</strong><br>
            <span>Total Tables Migrated: <strong>${data.total_tables}</strong></span> • 
            <span>Total Records Copied: <strong>${data.total_rows}</strong></span><br>
            <div style="font-size:0.75rem; margin-top:4px; color:var(--text-dim);">Your shared Turso Cloud Database now has 100% of the factory data!</div>
          </div>
        `;
      }
      showToast(`⚡ Migrated ${data.total_tables} tables (${data.total_rows} rows) to Turso!`, 'success');
      loadTursoStatusUI();
    } else {
      if (resBox) resBox.innerHTML = `<div class="alert alert-danger"><strong>Migration Failed:</strong> ${data.error}</div>`;
      showToast('Migration to Turso failed', 'danger');
    }
  } catch (e) {
    if (resBox) resBox.innerHTML = '<div class="alert alert-danger">Network error during migration.</div>';
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-upload"></i> Migrate Local DB to Cloud'; }
  }
}

// ----------------- BOM BUILDER -----------------
async function openBOMBuilder(skuId, skuName) {
  switchView('master_bom', { adminTab: 'bom' });
  const select = document.getElementById('bom-active-sku-select');
  if (select) select.value = skuId;
  loadSKUBOMDetails(skuId, skuName);
}

async function loadSKUBOMDetails(skuId, skuName) {
  if (!skuId) skuId = document.getElementById('bom-active-sku-select').value;
  if (!skuId) return;

  const container = document.getElementById('bom-items-container');
  container.innerHTML = '<div style="text-align:center; padding:2rem;"><i class="fas fa-spinner fa-spin"></i> Loading Bill of Materials...</div>';

  try {
    const res = await fetch(`/api/bom/${skuId}`);
    const data = await res.json();
    const sku = data.sku;
    const items = data.items;

    document.getElementById('bom-selected-sku-title').textContent = `${sku.sku_code} - ${sku.name}`;
    document.getElementById('bom-sku-meta').textContent = `Category: ${sku.category} • Pack Size: ${sku.pack_size} • Carton Size: ${sku.ctn_size} ${sku.uom_default}/Ctn`;

    if (!items || items.length === 0) {
      container.innerHTML = `
        <div style="text-align:center; padding:2rem; color:var(--text-dim);">
          No Raw Materials or Packaging Materials in this BOM yet.
          <br><button class="btn btn-primary" style="margin-top:1rem;" onclick="openAddBOMItemModal(${skuId})">
            <i class="fas fa-plus"></i> Add RM / PM to BOM
          </button>
        </div>
      `;
      return;
    }

    container.innerHTML = `
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem;">
        <span style="font-weight:600; font-size:0.85rem;">Materials in Formulation (${items.length} items)</span>
        <button class="btn btn-sm btn-primary" onclick="openAddBOMItemModal(${skuId})">
          <i class="fas fa-plus"></i> Add RM / PM to BOM
        </button>
      </div>
      <div class="table-responsive">
        <table class="data-table">
          <thead>
            <tr>
              <th>Material Name</th>
              <th>Type</th>
              <th>Rate / Carton</th>
              <th>Unit</th>
              <th>Unit Price (৳)</th>
              <th>Est. Cost / Ctn (৳)</th>
              <th>Notes / Formulation</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            ${items.map(it => `
              <tr>
                <td><strong>${it.material_name}</strong> <span style="font-size:0.72rem; color:var(--text-dim);">(${it.item_code})</span></td>
                <td><span class="pill ${it.material_type === 'RM' ? 'pill-safe' : 'pill-fast'}">${it.material_type}</span></td>
                <td>
                  <input type="number" step="0.0001" class="form-control" style="width:110px; font-weight:700;" value="${it.rate_per_ctn}" 
                         onchange="updateBOMItemRate(${skuId}, ${it.material_id}, this.value, '${it.bom_unit}')">
                </td>
                <td>${it.bom_unit}</td>
                <td>৳ ${it.unit_price}</td>
                <td><strong>৳ ${(it.rate_per_ctn * it.unit_price).toFixed(2)}</strong></td>
                <td><span style="font-size:0.75rem; color:var(--text-muted);">${it.notes || '-'}</span></td>
                <td>
                  <button class="btn btn-sm btn-danger" onclick="deleteBOMItem(${skuId}, ${it.material_id})">
                    <i class="fas fa-trash"></i>
                  </button>
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `;
  } catch (e) {
    container.innerHTML = '<div style="color:var(--danger); padding:1rem;">Failed to load BOM.</div>';
  }
}

async function updateBOMItemRate(skuId, matId, newRate, unit) {
  try {
    const res = await fetch(`/api/bom/${skuId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ material_id: matId, rate_per_ctn: parseFloat(newRate), unit })
    });
    const data = await res.json();
    if (data.success) {
      showToast('BOM rate updated!', 'success');
      loadSKUBOMDetails(skuId);
    }
  } catch (e) {
    showToast('Failed to update rate', 'danger');
  }
}

async function deleteBOMItem(skuId, matId) {
  if (!confirm('Remove this material from the BOM formulation?')) return;
  try {
    const res = await fetch(`/api/bom/${skuId}/${matId}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.success) {
      showToast('Material removed from BOM', 'success');
      loadSKUBOMDetails(skuId);
    }
  } catch (e) {
    showToast('Failed to remove material from BOM', 'danger');
  }
}

async function openAddBOMItemModal(skuId) {
  document.getElementById('bom-add-sku-id').value = skuId;
  const select = document.getElementById('bom-add-material-select');

  if (AppState.materials.length === 0) {
    const res = await fetch('/api/materials');
    AppState.materials = await res.json();
  }

  select.innerHTML = '<option value="">-- Choose RM or PM --</option>' +
    AppState.materials.map(m => `<option value="${m.id}" data-unit="${m.unit}">[${m.type}] ${m.name} (${m.unit})</option>`).join('');

  showModal('modal-add-bom-item');
}

async function submitAddBOMItem() {
  const skuId = document.getElementById('bom-add-sku-id').value;
  const select = document.getElementById('bom-add-material-select');
  const matId = select.value;
  const rate = parseFloat(document.getElementById('bom-add-rate-input').value) || 0;
  const unit = select.options[select.selectedIndex]?.getAttribute('data-unit') || 'Kg';
  const notes = document.getElementById('bom-add-notes-input').value;

  if (!matId || rate <= 0) {
    showToast('Please select a material and enter a positive rate', 'warning');
    return;
  }

  try {
    const res = await fetch(`/api/bom/${skuId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ material_id: parseInt(matId), rate_per_ctn: rate, unit, notes })
    });
    const data = await res.json();
    if (data.success) {
      hideModal('modal-add-bom-item');
      showToast('Material added to BOM!', 'success');
      loadSKUBOMDetails(skuId);
    }
  } catch (e) {
    showToast('Failed to add material to BOM', 'danger');
  }
}

// ----------------- 6. PROCUREMENT CONTROLLER -----------------
async function loadProcurementView() {
  const cat = AppState.selectedCategory;
  try {
    const res = await fetch(`/api/requisitions/purchase?category=${cat}`);
    const pos = await res.json();
    renderProcurementTable(pos);
  } catch (e) {
    console.error('Error loading procurement:', e);
  }
}

function renderProcurementTable(pos) {
  const tbody = document.getElementById('procurement-table-body');
  if (!tbody) return;

  if (!pos || pos.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; padding:2rem; color:var(--text-dim);">No purchase requisitions found for this category.</td></tr>`;
    return;
  }

  tbody.innerHTML = pos.map(p => {
    let statusClass = 'pill-critical';
    if (p.status === 'Received') statusClass = 'pill-safe';
    else if (p.status === 'In-Transit') statusClass = 'pill-fast';
    else if (p.status === 'Approved' || p.status === 'PO Issued') statusClass = 'pill-low';

    return `
      <tr>
        <td><strong>${p.po_no}</strong></td>
        <td>
          <div style="font-weight:600;">${p.material_name}</div>
          <div style="font-size:0.72rem; color:var(--text-dim);">${p.item_code} • ${p.supplier_name}</div>
        </td>
        <td><span class="pill ${p.type === 'RM' ? 'pill-safe' : 'pill-fast'}">${p.type}</span></td>
        <td><strong>${p.req_qty.toLocaleString()}</strong> ${p.unit}</td>
        <td>৳ ${p.unit_price}</td>
        <td><strong>৳ ${p.total_value.toLocaleString()}</strong></td>
        <td><span class="pill ${statusClass}">${p.status}</span></td>
        <td>${p.expected_delivery || `${p.lead_time_days} days`}</td>
        <td>
          <div style="display:flex; gap:0.35rem;">
            ${p.status === 'Pending' ? `
              <button class="btn btn-sm btn-primary" onclick="updatePOStatus(${p.id}, 'In-Transit')">
                <i class="fas fa-truck"></i> Mark Ordered
              </button>
            ` : ''}
            ${p.status === 'In-Transit' ? `
              <button class="btn btn-sm btn-success" onclick="openReceiveGRNModal(${p.id}, '${p.po_no}', '${p.material_name.replace(/'/g, "\\'")}', ${p.req_qty}, '${p.unit}')">
                <i class="fas fa-clipboard-check"></i> Receive GRN
              </button>
            ` : ''}
            ${p.status === 'Received' ? `
              <span style="color:var(--success); font-weight:700; font-size:0.75rem;"><i class="fas fa-check"></i> GRN Complete</span>
            ` : ''}
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

function openRaisePurchaseModal(matId, name, suggestedQty, price, supplier) {
  document.getElementById('raise-po-mat-id').value = matId;
  document.getElementById('raise-po-mat-name').textContent = name;
  document.getElementById('raise-po-qty').value = Math.max(10, Math.round(suggestedQty));
  document.getElementById('raise-po-price').value = price || 50;
  document.getElementById('raise-po-supplier').value = supplier || 'Direct Supplier';
  showModal('modal-raise-purchase');
}

async function submitRaisePurchase() {
  const matId = document.getElementById('raise-po-mat-id').value;
  const qty = parseFloat(document.getElementById('raise-po-qty').value) || 0;
  const price = parseFloat(document.getElementById('raise-po-price').value) || 0;
  const supplier = document.getElementById('raise-po-supplier').value;
  const prio = document.getElementById('raise-po-priority').value;

  try {
    const res = await fetch('/api/requisitions/purchase', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        material_id: matId,
        req_qty: qty,
        unit_price: price,
        supplier_name: supplier,
        priority: prio,
        month_year: AppState.selectedMonth
      })
    });
    const data = await res.json();
    if (data.success) {
      hideModal('modal-raise-purchase');
      showToast(`Purchase requisition ${data.po_no} raised!`, 'success');
      loadProcurementView();
      loadDashboard();
    }
  } catch (e) {
    showToast('Failed to raise purchase requisition', 'danger');
  }
}

async function updatePOStatus(poId, newStatus) {
  try {
    const res = await fetch(`/api/requisitions/purchase/${poId}/status`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: newStatus })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`PO status updated to ${newStatus}`, 'success');
      loadProcurementView();
      loadDashboard();
    }
  } catch (e) {
    showToast('Failed to update PO status', 'danger');
  }
}

function openReceiveGRNModal(poId, poNo, matName, reqQty, unit) {
  document.getElementById('grn-po-id').value = poId;
  document.getElementById('grn-po-no').textContent = poNo;
  document.getElementById('grn-mat-name').textContent = matName;
  document.getElementById('grn-qty-input').value = reqQty;
  document.getElementById('grn-unit-label').textContent = unit;
  showModal('modal-receive-grn');
}

async function submitReceiveGRN() {
  const poId = document.getElementById('grn-po-id').value;
  const qty = parseFloat(document.getElementById('grn-qty-input').value) || 0;
  const remarks = document.getElementById('grn-remarks').value;

  try {
    const res = await fetch(`/api/requisitions/purchase/${poId}/receive-grn`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ received_qty: qty, remarks })
    });
    const data = await res.json();
    if (data.success) {
      hideModal('modal-receive-grn');
      showToast(`Goods received! New store stock balance: ${data.new_stock.toLocaleString()}`, 'success');
      loadProcurementView();
      loadDashboard();
    }
  } catch (e) {
    showToast('Failed to process GRN', 'danger');
  }
}

// ----------------- 7. REPORTS CONTROLLER (SEPARATE TABS & COMPACT TOOLBAR) -----------------
function switchReportSubTab(tabName) {
  AppState.reportSubTab = tabName;
  const isForecast = tabName === 'forecast_prod';

  document.getElementById('tab-btn-forecast-prod')?.classList.toggle('active', isForecast);
  document.getElementById('tab-btn-stock-fsn')?.classList.toggle('active', !isForecast);

  const fSec = document.getElementById('report-subtab-forecast-prod');
  const sSec = document.getElementById('report-subtab-stock-fsn');
  if (fSec) fSec.style.display = isForecast ? 'block' : 'none';
  if (sSec) sSec.style.display = !isForecast ? 'block' : 'none';

  const btnLabel = document.getElementById('export-excel-btn-label');
  if (btnLabel) {
    btnLabel.textContent = isForecast ? 'Export Forecast vs Production (.xlsx)' : 'Export RM & PM Stock Summary (.xlsx)';
  }

  if (isForecast) {
    loadForecastVsProdReport();
  } else {
    loadStockSummaryReport();
  }
}

async function loadReportsView() {
  if (!AppState.reportSubTab) AppState.reportSubTab = 'forecast_prod';
  switchReportSubTab(AppState.reportSubTab);
}

function exportActiveReportExcel() {
  const isForecast = (AppState.reportSubTab || 'forecast_prod') === 'forecast_prod';
  const uom = AppState.displayUOM;
  const month = document.getElementById('report-month-select')?.value || AppState.selectedMonth;

  if (isForecast) {
    const cat = document.getElementById('report-category-select')?.value || AppState.selectedCategory;
    const mode = AppState.reportFilterMode || 'month';
    let url = `/api/reports/export/excel?section=forecast_vs_prod&category=${cat}&month=${month}&uom=${uom}`;
    if (mode === 'single') {
      const pDate = document.getElementById('report-single-date')?.value || '';
      if (pDate) url += `&plan_date=${pDate}`;
    } else if (mode === 'range') {
      const dFrom = document.getElementById('report-date-from')?.value || '';
      const dTo = document.getElementById('report-date-to')?.value || '';
      if (dFrom && dTo) url += `&date_from=${dFrom}&date_to=${dTo}`;
    }
    showToast('Downloading Forecast vs Production Excel report...', 'primary');
    window.location.href = url;
  } else {
    const cat = document.getElementById('report-stock-cat-select')?.value || AppState.selectedCategory;
    const url = `/api/reports/export/excel?section=stock_fsn&category=${cat}&month=${month}&uom=${uom}`;
    showToast('Downloading RM & PM Stock Summary Excel report...', 'primary');
    window.location.href = url;
  }
}

function setReportDateFilterMode(mode) {
  AppState.reportFilterMode = mode;
  document.getElementById('report-mode-month-btn')?.classList.toggle('active', mode === 'month');
  document.getElementById('report-mode-single-btn')?.classList.toggle('active', mode === 'single');
  document.getElementById('report-mode-range-btn')?.classList.toggle('active', mode === 'range');

  const monthCont = document.getElementById('report-filter-month-container');
  const singleCont = document.getElementById('report-filter-single-container');
  const rangeFromCont = document.getElementById('report-filter-range-from-container');
  const rangeToCont = document.getElementById('report-filter-range-to-container');
  const quickChips = document.getElementById('report-quick-chips-row');

  if (monthCont) monthCont.style.display = mode === 'month' ? '' : 'none';
  if (singleCont) singleCont.style.display = mode === 'single' ? '' : 'none';
  if (rangeFromCont) rangeFromCont.style.display = mode === 'range' ? '' : 'none';
  if (rangeToCont) rangeToCont.style.display = mode === 'range' ? '' : 'none';
  if (quickChips) quickChips.style.display = (mode === 'single' || mode === 'range') ? 'flex' : 'none';
}

function setReportQuickDate(type) {
  const inp = document.getElementById('report-single-date');
  if (!inp) return;
  const d = new Date();
  if (type === 'yesterday') {
    d.setDate(d.getDate() - 1);
  }
  inp.value = d.toISOString().split('T')[0];
  loadForecastVsProdReport();
}

function setReportQuickRange(type) {
  const fromInp = document.getElementById('report-date-from');
  const toInp = document.getElementById('report-date-to');
  if (!fromInp || !toInp) return;
  const today = new Date();
  toInp.value = today.toISOString().split('T')[0];

  if (type === 'week') {
    const f = new Date();
    f.setDate(f.getDate() - 7);
    fromInp.value = f.toISOString().split('T')[0];
  } else if (type === 'mtd') {
    fromInp.value = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-01`;
  } else if (type === 'full_month') {
    const y = today.getFullYear();
    const m = today.getMonth() + 1;
    const lastDay = new Date(y, m, 0).getDate();
    fromInp.value = `${y}-${String(m).padStart(2, '0')}-01`;
    toInp.value = `${y}-${String(m).padStart(2, '0')}-${String(lastDay).padStart(2, '0')}`;
  }
  loadForecastVsProdReport();
}

async function loadForecastVsProdReport() {
  const cat = document.getElementById('report-category-select')?.value || AppState.selectedCategory;
  const mode = AppState.reportFilterMode || 'month';
  const uom = AppState.displayUOM;
  const isCarton = uom === 'Carton';

  let url = `/api/reports/forecast-vs-production?category=${cat}&uom=${uom}`;
  let filterSummary = '';

  if (mode === 'single') {
    const pDate = document.getElementById('report-single-date')?.value || '2026-07-24';
    url += `&plan_date=${pDate}`;
    filterSummary = `Specific Date: ${pDate}`;
  } else if (mode === 'range') {
    const dFrom = document.getElementById('report-date-from')?.value || '2026-07-01';
    const dTo = document.getElementById('report-date-to')?.value || '2026-07-31';
    url += `&date_from=${dFrom}&date_to=${dTo}`;
    filterSummary = `Date Range: ${dFrom} to ${dTo}`;
  } else {
    const month = document.getElementById('report-month-select')?.value || AppState.selectedMonth;
    url += `&month=${month}`;
    filterSummary = `Full Month: ${month}`;
  }

  const badge = document.getElementById('report-filter-summary-badge');
  if (badge) badge.textContent = `Filter: ${filterSummary} (${cat === 'ALL' ? 'All Units' : 'Unit ' + cat})`;

  const titleSpan = document.getElementById('report-forecast-prod-title');
  if (titleSpan) titleSpan.textContent = `Forecast vs Production Report (${filterSummary})`;

  try {
    const res = await fetch(url);
    const data = await res.json();
    const tbody = document.getElementById('report-forecast-prod-tbody');
    if (!tbody) return;

    if (!data.items || data.items.length === 0) {
      tbody.innerHTML = '<tr><td colspan="11" style="text-align:center; color:var(--text-muted); padding:1.5rem;">No production or forecast data found for this selection.</td></tr>';
      return;
    }

    tbody.innerHTML = data.items.map(it => {
      const origFcVal = isCarton ? (it.original_forecast_ctn || it.forecast_ctn) : ((it.original_forecast_ctn || it.forecast_ctn) * it.ctn_size);
      const activeFcVal = isCarton ? it.forecast_ctn : (it.forecast_ctn * it.ctn_size);
      const planVal = isCarton ? it.planned_ctn : (it.planned_ctn * it.ctn_size);
      const actVal = isCarton ? it.actual_produced_ctn : (it.actual_produced_ctn * it.ctn_size);
      const varVal = actVal - activeFcVal;
      const uomLabel = isCarton ? 'Ctn' : (it.uom_default || 'Pcs');

      let statusBadge = '';
      if (it.forecast_achievement_pct >= 100) {
        statusBadge = '<span class="pill pill-safe"><i class="fas fa-check-circle"></i> Exceeded</span>';
      } else if (it.forecast_achievement_pct >= 80) {
        statusBadge = '<span class="pill pill-safe"><i class="fas fa-check"></i> On Target</span>';
      } else if (it.forecast_achievement_pct >= 50) {
        statusBadge = '<span class="pill pill-warn"><i class="fas fa-exclamation-circle"></i> In Progress</span>';
      } else {
        statusBadge = '<span class="pill pill-danger"><i class="fas fa-times-circle"></i> Shortfall</span>';
      }

      return `
        <tr>
          <td><strong>${it.sku_code}</strong></td>
          <td>
            <div style="font-weight:600;">${it.sku_name}</div>
            <div style="font-size:0.72rem; color:var(--text-dim);">${it.pack_size || ''} • ${it.ctn_size} Pcs/Ctn</div>
          </td>
          <td><span class="pill pill-safe">Unit ${it.category_code}</span></td>
          <td style="text-align:right; color:var(--text-muted);">${origFcVal.toLocaleString()} ${uomLabel}</td>
          <td style="text-align:right; font-weight:700; color:var(--primary);">
            ${activeFcVal.toLocaleString()} ${uomLabel}
          </td>
          <td style="text-align:center;">
            ${(it.revision_no || 0) > 0 ? `<button type="button" class="btn btn-xs pill pill-warn" onclick="openForecastRevisionsModal(${it.sku_id}, '${it.sku_name.replace(/'/g, "\\'")}')" style="cursor:pointer; border:none;" title="Click to view full revision history">Rev ${it.revision_no}</button>` : `<span class="pill pill-safe" style="font-size:0.72rem;">Rev 0</span>`}
          </td>
          <td style="text-align:right;">${planVal.toLocaleString()} ${uomLabel}</td>
          <td style="text-align:right; font-weight:700; color:var(--success);">${actVal.toLocaleString()} ${uomLabel}</td>
          <td style="text-align:right; font-weight:700; color:${varVal >= 0 ? 'var(--success)' : 'var(--danger)'};">
            ${varVal >= 0 ? '+' : ''}${varVal.toLocaleString()}
          </td>
          <td style="text-align:center;">
            <span class="pill ${it.forecast_achievement_pct >= 80 ? 'pill-safe' : (it.forecast_achievement_pct >= 50 ? 'pill-warn' : 'pill-danger')}" style="font-weight:700;">
              ${it.forecast_achievement_pct}%
            </span>
          </td>
          <td style="text-align:center;">${statusBadge}</td>
        </tr>
      `;
    }).join('');
  } catch (e) {
    console.error('Error loading forecast vs prod report:', e);
  }
}

async function loadStockSummaryReport() {
  const cat = document.getElementById('report-stock-cat-select')?.value || AppState.selectedCategory;
  try {
    const res = await fetch(`/api/reports/stock-summary?category=${cat}`);
    const items = await res.json();
    const tbody = document.getElementById('report-stock-summary-tbody');
    if (!tbody) return;

    if (!items || items.length === 0) {
      tbody.innerHTML = '<tr><td colspan="11" style="text-align:center; color:var(--text-muted); padding:1.5rem;">No RM/PM materials found for this selection.</td></tr>';
      return;
    }

    let totalVal = 0;
    let criticalCount = 0;

    tbody.innerHTML = items.map(m => {
      totalVal += (m.stock_value || 0);
      if (m.current_stock < m.safety_stock) criticalCount++;

      let fsnClass = 'pill-fast';
      if (m.fsn_status === 'Slow-Moving') fsnClass = 'pill-slow';
      else if (m.fsn_status === 'Non-Moving') fsnClass = 'pill-non-moving';

      return `
        <tr>
          <td><strong>${m.item_code}</strong></td>
          <td><strong>${m.name}</strong></td>
          <td><span class="pill ${m.type === 'RM' ? 'pill-safe' : 'pill-fast'}">${m.type}</span></td>
          <td>Unit ${m.category_code}</td>
          <td>${m.unit}</td>
          <td><strong>${m.current_stock.toLocaleString()}</strong></td>
          <td>${m.safety_stock.toLocaleString()}</td>
          <td>
            ${m.deficit_qty > 0 
              ? `<span class="pill pill-critical">-${m.deficit_qty}</span>` 
              : `<span class="pill pill-safe">Buffer OK</span>`}
          </td>
          <td>৳ ${m.unit_price}</td>
          <td><strong>৳ ${m.stock_value.toLocaleString()}</strong></td>
          <td><span class="pill ${fsnClass}">${m.fsn_status}</span></td>
        </tr>
      `;
    }).join('');

    const valEl = document.getElementById('report-kpi-total-val');
    if (valEl) valEl.textContent = `Total Valuation: ৳${Math.round(totalVal).toLocaleString()}`;
    const critEl = document.getElementById('report-kpi-critical-count');
    if (critEl) critEl.textContent = `Critical: ${criticalCount} Items`;
    const countBadge = document.getElementById('report-stock-count-badge');
    if (countBadge) countBadge.textContent = `${items.length} Materials Total • Live Balance`;
  } catch (e) {
    console.error('Error loading stock summary report:', e);
  }
}

// Dynamic Section-Specific Excel Export
function downloadSectionExcel(specificSection) {
  const cat = AppState.selectedCategory;
  const month = AppState.selectedMonth;
  const uom = AppState.displayUOM;
  
  let section = specificSection;
  if (!section) {
    if (AppState.currentView === 'store') {
      section = AppState.storeSubTab || 'store';
    } else {
      section = AppState.currentView || 'reports';
    }
  }
  
  window.location.href = `/api/reports/export/excel?section=${section}&category=${cat}&month=${month}&uom=${uom}`;
  showToast(`Downloading Excel report for ${section.toUpperCase()} with solid cell shades...`, 'success');
}

function downloadExcelReport() {
  downloadSectionExcel();
}

// Bulk Upload for Sales Forecast
function openBulkForecastModal() {
  const f = document.getElementById('bulk-forecast-file');
  if (f) f.value = '';
  showModal('modal-bulk-forecast');
}

async function submitBulkForecast() {
  const fileInput = document.getElementById('bulk-forecast-file');
  const month = document.getElementById('bulk-forecast-month')?.value || AppState.selectedMonth;
  if (!fileInput.files || fileInput.files.length === 0) {
    showToast('Please select an Excel (.xlsx) file to upload', 'warning');
    return;
  }
  const formData = new FormData();
  formData.append('file', fileInput.files[0]);
  formData.append('month', month);

  showToast('Uploading and updating sales forecast...', 'primary');
  try {
    const res = await fetch('/api/forecast/bulk-upload', {
      method: 'POST',
      body: formData
    });
    const data = await res.json();
    if (data.success) {
      hideModal('modal-bulk-forecast');
      showToast(data.message || `Uploaded forecast for ${data.count} items!`, 'success');
      loadForecastView();
      loadDashboard();
    } else {
      showToast(data.error || 'Failed to upload forecast', 'danger');
    }
  } catch (e) {
    showToast('Error uploading forecast file', 'danger');
  }
}

// Bulk Upload & Template for Production Plans
function downloadProductionTemplate() {
  const cat = AppState.selectedCategory;
  const month = AppState.selectedMonth;
  window.location.href = `/api/production/template?category=${cat}&month=${month}`;
  showToast('Downloading blank Production Plan template...', 'success');
}

function openBulkProductionModal() {
  const f = document.getElementById('bulk-prod-file');
  if (f) f.value = '';
  showModal('modal-bulk-production');
}

async function submitBulkProduction() {
  const fileInput = document.getElementById('bulk-prod-file');
  const month = document.getElementById('bulk-prod-month')?.value || AppState.selectedMonth;
  const cat = AppState.selectedCategory;
  if (!fileInput.files || fileInput.files.length === 0) {
    showToast('Please select an Excel (.xlsx) file to upload', 'warning');
    return;
  }
  const formData = new FormData();
  formData.append('file', fileInput.files[0]);
  formData.append('month', month);
  formData.append('category', cat);

  showToast('Uploading and importing production plans...', 'primary');
  try {
    const res = await fetch('/api/production/bulk-upload', {
      method: 'POST',
      body: formData
    });
    const data = await res.json();
    if (data.success) {
      hideModal('modal-bulk-production');
      showToast(data.message || `Imported ${data.count} production plans!`, 'success');
      loadProductionView();
      loadDashboard();
    } else {
      showToast(data.error || 'Failed to import production plans', 'danger');
    }
  } catch (e) {
    showToast('Error uploading production plan file', 'danger');
  }
}

// Super Admin Data Clear Tool
function openClearDataModal() {
  if (!isSuperAdminUser()) {
    showToast('Access Denied (নিরাপত্তা সীমাবদ্ধতা): Only Super Admin has permission to access the System Reset & Data Cleanup Tool.', 'danger');
    return;
  }
  const c = document.getElementById('clear-data-confirm-input');
  if (c) c.value = '';
  showModal('modal-clear-data');
}

async function submitClearData() {
  if (!isSuperAdminUser()) {
    showToast('Access Denied (নিরাপত্তা সীমাবদ্ধতা): Only Super Admin can perform data cleanup operations.', 'danger');
    return;
  }
  const section = document.getElementById('clear-data-section').value;
  const category = document.getElementById('clear-data-category').value;
  const rangeType = document.getElementById('clear-data-range-type').value;
  const fromDate = document.getElementById('clear-data-from-date').value;
  const toDate = document.getElementById('clear-data-to-date').value;
  const confirmText = document.getElementById('clear-data-confirm-input').value.trim();

  if (confirmText !== 'DELETE') {
    showToast('Please type DELETE in capital letters to confirm', 'warning');
    return;
  }

  if (rangeType === 'custom' && (!fromDate || !toDate)) {
    showToast('Please select both From Date and To Date', 'warning');
    return;
  }

  try {
    const res = await fetch('/api/admin/clear-data', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        section,
        category,
        range_type: rangeType,
        from_date: fromDate,
        to_date: toDate
      })
    });
    const data = await res.json();
    if (data.success) {
      hideModal('modal-clear-data');
      showToast('Data cleanup executed successfully!', 'success');
      switchView(AppState.currentView);
    } else {
      showToast(data.error || 'Failed to clear data', 'danger');
    }
  } catch (e) {
    showToast('Error during data cleanup', 'danger');
  }
}

function printPDFReport() {
  window.print();
}

// ----------------- MODAL & TOAST HELPERS -----------------
function showModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) modal.classList.add('show');
}

function hideModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) modal.classList.remove('show');
}

// Global modal backdrop click dismiss
window.addEventListener('click', (e) => {
  if (e.target && (e.target.classList.contains('modal-backdrop') || e.target.classList.contains('modal'))) {
    e.target.classList.remove('show');
  }
});

// ESC key closes any active modal
window.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    document.querySelectorAll('.modal-backdrop.show, .modal.show').forEach(m => m.classList.remove('show'));
  }
});

function showToast(message, type = 'primary') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `
    <i class="fas ${type === 'success' ? 'fa-check-circle' : (type === 'danger' ? 'fa-exclamation-triangle' : 'fa-info-circle')}" style="color:var(--${type});"></i>
    <div style="flex:1; font-size:0.82rem; font-weight:600;">${message}</div>
  `;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(100%)';
    toast.style.transition = 'all 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// Requisition Detail & Issuance
async function viewRequisitionDetail(reqId) {
  try {
    const res = await fetch(`/api/requisitions/production/${reqId}`);
    const data = await res.json();
    const r = data.requisition;
    const items = data.items;

    document.getElementById('modal-req-title').textContent = `Requisition #${r.req_no} Details`;
    document.getElementById('modal-req-subtitle').textContent = `Plan: ${r.plan_no} • ${r.sku_name} (${r.planned_ctn} Cartons)`;

    const tbody = document.getElementById('modal-req-items-body');
    tbody.innerHTML = items.map(it => {
      const pending = it.required_qty - it.issued_qty;
      const canFulfill = it.current_stock >= pending;

      return `
        <tr>
          <td><strong>${it.material_name}</strong></td>
          <td><span class="pill ${it.material_type === 'RM' ? 'pill-safe' : 'pill-fast'}">${it.material_type}</span></td>
          <td><strong>${it.required_qty}</strong> ${it.unit}</td>
          <td>${it.issued_qty} ${it.unit}</td>
          <td>
            <span style="font-weight:700; color:${canFulfill ? 'var(--text-main)' : 'var(--danger)'};">
              ${it.current_stock.toLocaleString()}
            </span> ${it.unit}
          </td>
          <td>
            ${it.status === 'Issued' ? '<span class="pill pill-safe">Issued</span>' : (canFulfill ? '<span class="pill pill-safe">In Stock</span>' : '<span class="pill pill-critical">Deficit</span>')}
          </td>
          <td>
            ${pending > 0 && canFulfill ? `
              <button class="btn btn-sm btn-success" onclick="issueSingleItem(${r.id}, ${it.id}, ${pending})">
                Issue ${pending}
              </button>
            ` : (it.status === 'Issued' ? '<i class="fas fa-check" style="color:var(--success);"></i>' : '-')}
          </td>
        </tr>
      `;
    }).join('');

    const issueAllBtn = document.getElementById('modal-req-issue-all-btn');
    if (issueAllBtn) {
      issueAllBtn.style.display = (r.status === 'Issued') ? 'none' : 'block';
      issueAllBtn.onclick = () => issueAllRequisitionMaterials(r.id);
    }
    showModal('modal-requisition-detail');
  } catch (e) {
    showToast('Failed to load requisition detail', 'danger');
  }
}

async function issueAllRequisitionMaterials(reqId) {
  if (!confirm('Issue all required materials to Production? This will automatically deduct stock from Store inventory.')) return;
  try {
    const res = await fetch(`/api/requisitions/production/${reqId}/issue`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({})
    });
    const data = await res.json();
    if (data.success) {
      showToast('Materials issued! Store stock automatically deducted.', 'success');
      hideModal('modal-requisition-detail');
      loadIncomingRequisitions();
      loadStoreStock();
      loadDashboard();
    }
  } catch (e) {
    showToast('Failed to issue materials', 'danger');
  }
}

async function issueSingleItem(reqId, itemId, qty) {
  try {
    const res = await fetch(`/api/requisitions/production/${reqId}/issue`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: [{ item_id: itemId, issue_qty: qty }] })
    });
    const data = await res.json();
    if (data.success) {
      showToast('Item issued and deducted from store inventory', 'success');
      viewRequisitionDetail(reqId);
      loadStoreStock();
      loadDashboard();
    }
  } catch (e) {
    showToast('Failed to issue item', 'danger');
  }
}

async function triggerAutoReplenishment() {
  if (!confirm(`Auto-generate purchase requisitions for all critical items in Unit ${AppState.selectedCategory}?`)) return;
  try {
    const res = await fetch('/api/requisitions/purchase/auto-generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ month: AppState.selectedMonth, category: AppState.selectedCategory })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Generated ${data.generated_count} purchase requisitions for Supply Chain!`, 'success');
      loadStoreStock();
      loadDashboard();
    }
  } catch (e) {
    showToast('Failed to auto-generate requisitions', 'danger');
  }
}

function openEditSafetyStockModal(matId, name, currentSafety) {
  document.getElementById('safety-mat-id').value = matId;
  document.getElementById('safety-mat-name').textContent = name;
  document.getElementById('safety-stock-input').value = currentSafety;
  showModal('modal-edit-safety-stock');
}

async function submitEditSafetyStock() {
  const matId = document.getElementById('safety-mat-id').value;
  const safety = parseFloat(document.getElementById('safety-stock-input').value) || 0;
  try {
    const res = await fetch('/api/store/safety-stock', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ material_id: matId, safety_stock: safety })
    });
    const data = await res.json();
    if (data.success) {
      hideModal('modal-edit-safety-stock');
      showToast('Safety stock updated!', 'success');
      loadStoreStock();
      loadDashboard();
    }
  } catch (e) {
    showToast('Failed to update safety stock', 'danger');
  }
}

function openAdjustStockModal(matId, name, currentStock) {
  document.getElementById('adjust-mat-id').value = matId;
  document.getElementById('adjust-mat-name').textContent = name;
  document.getElementById('adjust-current-display').textContent = currentStock;
  document.getElementById('adjust-new-stock').value = currentStock;
  showModal('modal-adjust-stock');
}

async function submitAdjustStock() {
  const matId = document.getElementById('adjust-mat-id').value;
  const newStock = parseFloat(document.getElementById('adjust-new-stock').value) || 0;
  const notes = document.getElementById('adjust-notes').value;
  try {
    const res = await fetch('/api/store/adjust', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ material_id: matId, new_stock: newStock, notes })
    });
    const data = await res.json();
    if (data.success) {
      hideModal('modal-adjust-stock');
      showToast('Stock adjusted and audit logged!', 'success');
      loadStoreStock();
      loadDashboard();
    }
  } catch (e) {
    showToast('Failed to adjust stock', 'danger');
  }
}

async function openCreatePlanModal() {
  const select = document.getElementById('plan-sku-select');
  const cat = AppState.selectedCategory;
  const res = await fetch(`/api/skus?category=${cat}`);
  const skus = await res.json();
  select.innerHTML = '<option value="">-- Select Finished Good (SKU) --</option>' + 
    skus.map(s => `<option value="${s.id}">${s.sku_code} - ${s.name} (${s.pack_size || ''})</option>`).join('');

  document.getElementById('plan-cartons-input').value = 500;
  document.getElementById('plan-date-input').value = new Date().toISOString().split('T')[0];
  document.getElementById('plan-notes-input').value = 'Sales forecast batch run';
  calculatePlanBOMRequirement();
  showModal('modal-create-plan');
}

async function calculatePlanBOMRequirement() {
  const skuId = parseInt(document.getElementById('plan-sku-select').value);
  const cartons = parseFloat(document.getElementById('plan-cartons-input').value) || 0;
  const container = document.getElementById('plan-bom-preview-container');

  if (!skuId || cartons <= 0) {
    container.innerHTML = '<div style="color:var(--text-dim); text-align:center; padding:1rem;">Select a SKU and carton quantity to preview RM/PM requirements.</div>';
    return;
  }

  container.innerHTML = '<div style="text-align:center; padding:1rem;"><i class="fas fa-spinner fa-spin"></i> Calculating BOM requirements...</div>';
  try {
    const res = await fetch('/api/bom/calculate-requirement', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: [{ sku_id: skuId, cartons }] })
    });
    const data = await res.json();
    const reqs = data.requirements;

    if (!reqs || reqs.length === 0) {
      container.innerHTML = '<div style="color:var(--warning); text-align:center; padding:1rem;">No BOM defined for this SKU.</div>';
      return;
    }

    container.innerHTML = `
      <div style="margin-bottom:0.75rem; font-size:0.8rem; font-weight:600; color:var(--text-muted); display:flex; justify-content:space-between;">
        <span>Required Raw & Packaging Materials (${reqs.length} items)</span>
        <span>Cartons: <strong>${cartons}</strong></span>
      </div>
      <div class="table-responsive" style="max-height:220px; overflow-y:auto;">
        <table class="data-table" style="font-size:0.78rem;">
          <thead>
            <tr>
              <th>Material</th>
              <th>Type</th>
              <th>Req Qty</th>
              <th>In Store</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            ${reqs.map(r => `
              <tr>
                <td><strong>${r.name}</strong></td>
                <td><span class="pill ${r.type === 'RM' ? 'pill-safe' : 'pill-fast'}">${r.type}</span></td>
                <td><strong>${r.required_qty}</strong> ${r.unit}</td>
                <td>${r.current_stock.toLocaleString()} ${r.unit}</td>
                <td>
                  ${r.stock_sufficient 
                    ? '<span class="pill pill-safe"><i class="fas fa-check"></i> Available</span>' 
                    : `<span class="pill pill-critical">Deficit: -${r.deficit}</span>`}
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `;
  } catch (e) {
    container.innerHTML = '<div style="color:var(--danger); padding:1rem;">Error calculating requirements.</div>';
  }
}

async function submitCreatePlan() {
  const skuId = parseInt(document.getElementById('plan-sku-select').value);
  const plannedCtn = parseFloat(document.getElementById('plan-cartons-input').value) || 0;
  const planDate = document.getElementById('plan-date-input').value;
  const notes = document.getElementById('plan-notes-input').value;
  const autoReq = document.getElementById('plan-auto-req-check').checked;

  if (!skuId || plannedCtn <= 0) {
    showToast('Please select a SKU and valid carton quantity', 'warning');
    return;
  }

  try {
    const res = await fetch('/api/production/plans', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sku_id: skuId,
        planned_ctn: plannedCtn,
        plan_date: planDate,
        month_year: AppState.selectedMonth,
        notes,
        auto_create_requisition: autoReq
      })
    });
    const data = await res.json();
    if (data.success) {
      hideModal('modal-create-plan');
      showToast(`Production Plan ${data.plan_no} created successfully!`, 'success');
      loadProductionView();
      loadDashboard();
    }
  } catch (e) {
    showToast('Failed to create production plan', 'danger');
  }
}

function openUpdateOutputModal(planId, planNo, plannedCtn, currentOutput) {
  document.getElementById('update-output-plan-id').value = planId;
  document.getElementById('update-output-plan-no').textContent = planNo;
  document.getElementById('update-output-planned').textContent = `${plannedCtn} Cartons`;
  document.getElementById('update-output-qty').value = currentOutput || plannedCtn;
  showModal('modal-update-output');
}

async function submitUpdateOutput() {
  const planId = document.getElementById('update-output-plan-id').value;
  const outputQty = parseFloat(document.getElementById('update-output-qty').value) || 0;
  const status = document.getElementById('update-output-status').value;

  try {
    const res = await fetch(`/api/production/plans/${planId}/update-output`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ actual_produced_ctn: outputQty, status })
    });
    const data = await res.json();
    if (data.success) {
      hideModal('modal-update-output');
      showToast('Actual production output updated!', 'success');
      loadProductionView();
      loadDashboard();
    }
  } catch (e) {
    showToast('Failed to update production output', 'danger');
  }
}

function openCreateSKUModal() {
  document.getElementById('form-create-sku')?.reset();
  showModal('modal-create-sku');
}

async function submitCreateSKU() {
  const name = document.getElementById('new-sku-name').value;
  const code = document.getElementById('new-sku-code').value;
  const cat = document.getElementById('new-sku-category').value;
  const pack = document.getElementById('new-sku-pack').value;
  const ctn = parseInt(document.getElementById('new-sku-ctn-size').value) || 12;
  const uom = document.getElementById('new-sku-uom').value || 'Packet';
  const catCode = document.getElementById('new-sku-category-code').value || AppState.selectedCategory;

  if (!name) {
    showToast('SKU Name is required', 'warning');
    return;
  }

  try {
    const res = await fetch('/api/skus', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, sku_code: code, category: cat, pack_size: pack, ctn_size: ctn, uom_default: uom, category_code: catCode })
    });
    const data = await res.json();
    if (data.success) {
      hideModal('modal-create-sku');
      showToast(`SKU ${data.sku_code} created!`, 'success');
      loadAdminSKUs();
    }
  } catch (e) {
    showToast('Failed to create SKU', 'danger');
  }
}

function openCreateMaterialModal() {
  document.getElementById('form-create-material')?.reset();
  showModal('modal-create-material');
}

async function submitCreateMaterial() {
  const name = document.getElementById('new-mat-name').value;
  const code = document.getElementById('new-mat-code').value;
  const type = document.getElementById('new-mat-type').value;
  const unit = document.getElementById('new-mat-unit').value;
  const price = parseFloat(document.getElementById('new-mat-price').value) || 0;
  const stock = parseFloat(document.getElementById('new-mat-stock').value) || 0;
  const safety = parseFloat(document.getElementById('new-mat-safety').value) || 0;
  const supplier = document.getElementById('new-mat-supplier').value;
  const lead = parseInt(document.getElementById('new-mat-lead').value) || 7;
  const catCode = document.getElementById('new-mat-cat-code').value || AppState.selectedCategory;

  if (!name) {
    showToast('Material Name is required', 'warning');
    return;
  }

  try {
    const res = await fetch('/api/materials', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name, item_code: code, type, unit, unit_price: price,
        current_stock: stock, safety_stock: safety, supplier_name: supplier,
        lead_time_days: lead, category_code: catCode
      })
    });
    const data = await res.json();
    if (data.success) {
      hideModal('modal-create-material');
      showToast(`Material ${data.item_code} created!`, 'success');
      loadAdminMaterials();
      loadStoreStock();
      loadDashboard();
    }
  } catch (e) {
    showToast('Failed to create material', 'danger');
  }
}



// ----------------- REQUIREMENT 5: PRODUCTION FLOOR STOCK MODAL -----------------
async function openFloorStockModal() {
  const container = document.getElementById('floor-stock-modal-body');
  if (container) container.innerHTML = '<div style="text-align:center; padding:2rem;"><i class="fas fa-spinner fa-spin"></i> Loading production floor stock...</div>';
  showModal('modal-floor-stock');

  const cat = AppState.selectedCategory;
  try {
    const res = await fetch(`/api/production/floor-stock?category=${cat}`);
    const items = await res.json();

    if (!items || items.length === 0) {
      container.innerHTML = '<div style="text-align:center; padding:2rem; color:var(--text-dim);">No materials found for this unit.</div>';
      return;
    }

    const rowsHtml = items.map(m => `
      <tr>
        <td><strong>${m.item_code}</strong></td>
        <td>
          <div style="font-weight:600;">${m.name}</div>
          <div style="font-size:0.72rem; color:var(--text-dim);">${m.supplier_name || 'Standard'}</div>
        </td>
        <td><span class="pill ${m.type === 'RM' ? 'pill-safe' : 'pill-fast'}">${m.type}</span></td>
        <td style="text-align:right;">${m.godown_stock.toLocaleString()} ${m.unit}</td>
        <td style="text-align:right;">
          <input type="number" step="0.1" class="form-control form-control-sm floor-stock-input" style="width:110px; font-weight:700; text-align:right;" 
                 id="floor-stock-val-${m.id}" value="${m.floor_stock || 0}">
        </td>
        <td style="text-align:right; font-weight:700; color:var(--primary);">${m.total_plant_stock.toLocaleString()} ${m.unit}</td>
        <td>
          <button class="btn btn-xs btn-primary" onclick="submitFloorStockAudit(${m.id})">
            <i class="fas fa-save"></i> Save Count
          </button>
        </td>
      </tr>
    `).join('');

    container.innerHTML = `
      <div style="background:var(--bg-tertiary); border:1px solid var(--border-color); border-radius:8px; padding:0.75rem 1rem; margin-bottom:1rem; font-size:0.82rem; display:flex; justify-content:space-between; align-items:center;">
        <div>
          <i class="fas fa-info-circle" style="color:var(--cyan);"></i>
          <strong>Floor Stock (WIP):</strong> Production ফ্লোরে অবস্থিত আনইউজড কাঁচামাল ও প্যাকেজিং ম্যাটেরিয়ালের শিফট অনুযায়ী ফিজিক্যাল কাউন্ট আপডেট করুন।
        </div>
        <span class="pill pill-safe">Unit ${cat}</span>
      </div>
      <div class="table-responsive" style="max-height:360px; overflow-y:auto;">
        <table class="data-table">
          <thead>
            <tr>
              <th>Item Code</th>
              <th>Material Name</th>
              <th>Type</th>
              <th style="text-align:right;">Store Godown</th>
              <th style="text-align:right;">Floor Stock (WIP)</th>
              <th style="text-align:right;">Total In-Plant</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            ${rowsHtml}
          </tbody>
        </table>
      </div>
    `;
  } catch (e) {
    container.innerHTML = '<div style="color:var(--danger); padding:1.5rem; text-align:center;">Failed to load floor stock.</div>';
  }
}

async function submitFloorStockAudit(matId) {
  const input = document.getElementById(`floor-stock-val-${matId}`);
  const val = parseFloat(input?.value) || 0;

  try {
    const res = await fetch('/api/production/floor-stock/log', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        material_id: matId,
        actual_floor_stock: val,
        shift: 'Day',
        notes: 'Shift physical floor audit'
      })
    });
    const data = await res.json();
    if (data.success) {
      showToast('Floor stock updated successfully!', 'success');
      loadStoreStock();
    } else {
      showToast(data.error || 'Failed to update floor stock', 'danger');
    }
  } catch (e) {
    showToast('Network error while saving floor stock', 'danger');
  }
}

// ----------------- REQUIREMENT 7: MRR BLANK TEMPLATE & BULK UPLOAD -----------------
function downloadMRRBlankTemplate() {
  const cat = AppState.selectedCategory;
  window.location.href = `/api/store/mrr/template?category=${cat}`;
  showToast('Downloading MRR Stock Inward Blank Template with solid cell shading...', 'success');
}

function openBulkMRRModal() {
  const f = document.getElementById('bulk-mrr-file');
  if (f) f.value = '';
  const status = document.getElementById('bulk-mrr-status');
  if (status) status.style.display = 'none';
  showModal('modal-bulk-mrr');
}

async function submitBulkMRR() {
  const fInp = document.getElementById('bulk-mrr-file');
  if (!fInp || !fInp.files || fInp.files.length === 0) {
    showToast('Please choose an Excel file to upload.', 'warning');
    return;
  }
  const file = fInp.files[0];
  const btn = document.getElementById('btn-submit-bulk-mrr');
  const statusEl = document.getElementById('bulk-mrr-status');

  if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Processing Inward...'; }
  if (statusEl) { statusEl.style.display = 'block'; statusEl.textContent = 'Processing Excel file and updating store stock...'; statusEl.style.color = 'var(--text-muted)'; }

  const formData = new FormData();
  formData.append('file', file);

  try {
    const res = await fetch('/api/store/mrr/bulk-upload', {
      method: 'POST',
      body: formData
    });
    const data = await res.json();

    if (data.success) {
      if (statusEl) {
        statusEl.style.color = 'var(--success)';
        statusEl.innerHTML = `<i class="fas fa-check-circle"></i> ${data.message}`;
      }
      showToast(`⚡ Processed ${data.processed_count} MRRs successfully!`, 'success');
      loadMRREntries();
      loadStoreStock();
      loadDashboard();
      setTimeout(() => { hideModal('modal-bulk-mrr'); }, 1500);
    } else {
      if (statusEl) {
        statusEl.style.color = 'var(--danger)';
        statusEl.innerHTML = `<i class="fas fa-exclamation-circle"></i> ${data.error || 'Failed to process MRR file.'}`;
      }
      showToast('Bulk upload failed', 'danger');
    }
  } catch (e) {
    if (statusEl) {
      statusEl.style.color = 'var(--danger)';
      statusEl.innerHTML = '<i class="fas fa-exclamation-circle"></i> Connection error while uploading.';
    }
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-cloud-upload-alt"></i> Upload & Process Inward'; }
  }
}

// ----------------- ENHANCED POLOXY ERP SYNC HANDLERS -----------------
let syncActionType = 'both';

function setSyncAction(action) {
  syncActionType = action;
  document.querySelectorAll('.sync-action-btn').forEach(b => b.classList.remove('active'));
  const btn = document.getElementById(`btn-sync-action-${action === 'actual_output' ? 'output' : (action === 'opening_stock' ? 'opening' : 'both')}`);
  if (btn) btn.classList.add('active');
}



// ----------------- REQUIREMENT 10: UNIFIED SINGLE DATE & DATE RANGE CONTROLLER -----------------
const UnifiedDateState = {
  mode: 'month', // 'month', 'single', 'range'
  month: '2026-07',
  singleDate: '2026-07-24',
  rangeFrom: '2026-07-01',
  rangeTo: '2026-07-31',
  displayLabel: '📅 July 2026'
};

function toggleUnifiedDateDropdown(btn) {
  const container = btn.closest('.unified-date-dropdown-container');
  const menu = container.querySelector('.unified-date-dropdown-menu');
  const isShown = menu.classList.contains('show');
  document.querySelectorAll('.unified-date-dropdown-menu').forEach(m => m.classList.remove('show'));
  if (!isShown) menu.classList.add('show');
}

function setDropdownDateMode(mode, btn) {
  UnifiedDateState.mode = mode;
  const menu = btn.closest('.unified-date-dropdown-menu');
  menu.querySelectorAll('.date-mode-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');

  const pMonth = menu.querySelector('.pane-month');
  const pSingle = menu.querySelector('.pane-single');
  const pRange = menu.querySelector('.pane-range');

  if (pMonth) pMonth.style.display = mode === 'month' ? 'block' : 'none';
  if (pSingle) pSingle.style.display = mode === 'single' ? 'block' : 'none';
  if (pRange) pRange.style.display = mode === 'range' ? 'block' : 'none';
}

function setQuickDateInput(type, btn) {
  const menu = btn.closest('.unified-date-dropdown-menu');
  const inp = menu.querySelector('.unified-date-single-input');
  const today = new Date().toISOString().split('T')[0];
  if (type === 'today') {
    inp.value = today;
  } else if (type === 'yesterday') {
    const y = new Date();
    y.setDate(y.getDate() - 1);
    inp.value = y.toISOString().split('T')[0];
  }
}

function setQuickDateRange(type, btn) {
  const menu = btn.closest('.unified-date-dropdown-menu');
  const fromInp = menu.querySelector('.unified-date-from-input');
  const toInp = menu.querySelector('.unified-date-to-input');
  const now = new Date();
  const toStr = now.toISOString().split('T')[0];
  
  if (type === 'week') {
    const w = new Date();
    w.setDate(w.getDate() - 7);
    fromInp.value = w.toISOString().split('T')[0];
    toInp.value = toStr;
  } else if (type === 'mtd') {
    fromInp.value = `${toStr.substring(0, 7)}-01`;
    toInp.value = toStr;
  } else if (type === 'full_month') {
    fromInp.value = '2026-07-01';
    toInp.value = '2026-07-31';
  }
}

function confirmUnifiedDateFilter(btn) {
  const menu = btn.closest('.unified-date-dropdown-menu');
  const container = btn.closest('.unified-date-dropdown-container');
  const labelEl = container.querySelector('.unified-date-label');
  
  if (UnifiedDateState.mode === 'month') {
    const mVal = menu.querySelector('.unified-month-select').value;
    UnifiedDateState.month = mVal;
    AppState.selectedMonth = mVal;
    UnifiedDateState.displayLabel = `📅 ${mVal}`;
  } else if (UnifiedDateState.mode === 'single') {
    const sVal = menu.querySelector('.unified-date-single-input').value;
    UnifiedDateState.singleDate = sVal;
    UnifiedDateState.displayLabel = `📅 Date: ${sVal}`;
  } else if (UnifiedDateState.mode === 'range') {
    const fVal = menu.querySelector('.unified-date-from-input').value;
    const tVal = menu.querySelector('.unified-date-to-input').value;
    UnifiedDateState.rangeFrom = fVal;
    UnifiedDateState.rangeTo = tVal;
    UnifiedDateState.displayLabel = `📅 ${fVal} to ${tVal}`;
  }

  // Update all labels on page
  document.querySelectorAll('.unified-date-label').forEach(lbl => {
    lbl.textContent = UnifiedDateState.displayLabel;
  });

  menu.classList.remove('show');
  
  if (AppState.currentView === 'production') loadProductionView();
  else if (AppState.currentView === 'forecast') loadForecastView();
  else if (AppState.currentView === 'reports') loadReportsView();
  else if (AppState.currentView === 'dashboard') loadDashboard();
}

document.addEventListener('click', (e) => {
  if (!e.target.closest('.unified-date-dropdown-container')) {
    document.querySelectorAll('.unified-date-dropdown-menu').forEach(m => m.classList.remove('show'));
  }
});

// ----------------- SUPER ADMIN EXCLUSIVE: FORECAST BOM MODAL -----------------
async function openForecastBOMModal(skuId, skuName) {
  if (!isSuperAdminUser()) {
    showToast('Access Denied (নিরাপত্তা সীমাবদ্ধতা): Only Super Admin has permission to view or edit Bill of Materials (BOM) specifications.', 'danger');
    return;
  }

  const titleEl = document.getElementById('forecast-bom-modal-title');
  const bodyEl = document.getElementById('forecast-bom-modal-body');
  if (titleEl) titleEl.textContent = `BOM Specification: ${skuName}`;
  if (bodyEl) bodyEl.innerHTML = '<div style="text-align:center; padding:2rem;"><i class="fas fa-spinner fa-spin"></i> Loading Bill of Materials from secure database...</div>';
  showModal('modal-view-bom');

  try {
    const res = await fetch(`/api/bom/${skuId}`);
    if (res.status === 403 || res.status === 401) {
      bodyEl.innerHTML = '<div class="alert alert-danger" style="margin:1rem;"><i class="fas fa-lock"></i> <strong>Access Denied:</strong> Only Super Admin can view Bill of Materials (BOM) specifications.</div>';
      return;
    }
    const data = await res.json();
    const items = data.items || [];
    if (items.length === 0) {
      bodyEl.innerHTML = '<div style="text-align:center; padding:2rem; color:var(--text-dim);">No BOM formulation items defined for this SKU.</div>';
      return;
    }

    const rmItems = items.filter(i => i.material_type === 'RM');
    const pmItems = items.filter(i => i.material_type === 'PM');

    const renderTable = (list, typeName) => {
      if (list.length === 0) return `<div style="font-size:0.8rem; color:var(--text-dim); padding:0.5rem;">No ${typeName} materials defined.</div>`;
      return `
        <table class="data-table" style="margin-bottom:1rem;">
          <thead>
            <tr>
              <th>Item Code</th>
              <th>Material Name</th>
              <th style="text-align:right;">Standard Rate (per Ctn)</th>
              <th style="text-align:right;">Current Godown Stock</th>
              <th style="text-align:right;">Unit Price</th>
            </tr>
          </thead>
          <tbody>
            ${list.map(it => `
              <tr>
                <td><strong>${it.item_code}</strong></td>
                <td>${it.material_name}</td>
                <td style="text-align:right; font-weight:700; color:var(--primary);">${it.rate_per_ctn} ${it.bom_unit || it.material_unit}</td>
                <td style="text-align:right;">${(it.current_stock || 0).toLocaleString()} ${it.material_unit}</td>
                <td style="text-align:right;">৳ ${(it.unit_price || 0).toFixed(2)}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
    };

    bodyEl.innerHTML = `
      <div style="background:rgba(30,58,138,0.08); border:1px solid rgba(30,58,138,0.25); border-radius:8px; padding:0.75rem 1rem; margin-bottom:1rem; display:flex; justify-content:space-between; align-items:center;">
        <div>
          <span class="pill pill-safe"><i class="fas fa-shield-alt"></i> Super Admin Exclusive View</span>
          <strong style="margin-left:0.5rem; color:var(--text-main); font-size:0.9rem;">${skuName}</strong>
        </div>
        <button class="btn btn-sm btn-primary" onclick="hideModal('modal-view-bom'); switchView('master_bom', { adminTab: 'bom' });">
          <i class="fas fa-edit"></i> Edit in Master BOM
        </button>
      </div>
      <h4 style="margin:0.5rem 0; font-size:0.9rem; color:var(--text-main);"><i class="fas fa-seedling" style="color:var(--success);"></i> Raw Materials (RM):</h4>
      ${renderTable(rmItems, 'Raw Materials')}
      <h4 style="margin:1rem 0 0.5rem 0; font-size:0.9rem; color:var(--text-main);"><i class="fas fa-box" style="color:var(--warning);"></i> Packaging Materials (PM):</h4>
      ${renderTable(pmItems, 'Packaging Materials')}
    `;
  } catch (e) {
    bodyEl.innerHTML = '<div style="color:var(--danger); padding:1.5rem; text-align:center;">Failed to fetch BOM details.</div>';
  }
}
