import { chromium } from 'playwright';

(async () => {
  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext();
  const page = await context.newPage();

  page.on('console', msg => {
    if (msg.type() === 'error') {
      console.log('BROWSER ERROR:', msg.text());
    }
  });

  console.log('Step 1: Navigate to app');
  await page.goto('http://localhost:18051', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: 'test-results/01_initial.png', fullPage: true });

  // Step 2: Switch to register mode
  console.log('Step 2: Switching to register mode');
  const registerTab = await page.$('button:has-text("注册")');
  if (registerTab) {
    await registerTab.click();
    await page.waitForTimeout(500);
  }
  await page.screenshot({ path: 'test-results/02_register_tab.png', fullPage: true });

  // Step 3: Fill registration form
  console.log('Step 3: Filling registration form');
  const usernameInput = await page.$('input[placeholder="输入用户名"]');
  const passwordInput = await page.$('input[placeholder="输入密码（至少 6 位）"]');

  if (usernameInput && passwordInput) {
    const timestamp = Date.now();
    const username = `testuser${timestamp}`;
    const password = 'Test123456';

    await usernameInput.fill(username);
    await passwordInput.fill(password);
    console.log(`Filled form with username: ${username}`);

    await page.screenshot({ path: 'test-results/03_filled_form.png', fullPage: true });

    // Submit registration
    const submitBtn = await page.$('button[type="submit"]:has-text("注册")');
    if (submitBtn) {
      console.log('Submitting registration...');
      await submitBtn.click();
      await page.waitForTimeout(4000);
    }
  }

  await page.screenshot({ path: 'test-results/04_after_auth.png', fullPage: true });

  // Check if we're now logged in (should see main app)
  const sidebar = await page.$('aside');
  console.log(`Sidebar found: ${!!sidebar}`);

  // Step 4: Find settings link
  console.log('Step 4: Looking for settings navigation');

  // Look for settings button in sidebar - it might be a button with shield icon
  const sidebarHTML = sidebar ? await sidebar.innerHTML() : '';
  console.log('Sidebar contains settings:', sidebarHTML.includes('设置') || sidebarHTML.includes('Settings'));

  // Try clicking on settings element
  const settingsLink = await page.$('aside button:has-text("设置"), nav button:has-text("设置"), [title*="设置"]');
  if (settingsLink) {
    console.log('Found settings button, clicking...');
    await settingsLink.click();
  } else {
    // Look for settings icon (ShieldCheck from lucide)
    const settingsIcon = await page.$('[class*="ShieldCheck"], svg[class*="shield"]');
    if (settingsIcon) {
      const parent = await settingsIcon.evaluateHandle(el => el.closest('button'));
      if (parent) {
        console.log('Found settings via icon, clicking parent button');
        await parent.click();
      }
    }
  }

  await page.waitForTimeout(2000);
  await page.screenshot({ path: 'test-results/05_settings_view.png', fullPage: true });

  // Check if we're on settings page
  const pageContent = await page.content();
  const isOnSettings = pageContent.includes('模型配置') || pageContent.includes('配置列表');
  console.log(`On settings page: ${isOnSettings}`);

  // Wait for API response and loading to complete
  await page.waitForTimeout(2000);

  // Look for config list
  const configArticles = await page.$$('article');
  console.log(`Found ${configArticles.length} config articles`);

  // Step 5: If no configs exist, create one first
  if (configArticles.length === 0) {
    console.log('Step 5: Creating a new config');

    const nameInput = await page.$('input[placeholder*="配置名称"], input[placeholder*="公司网关"]');
    if (nameInput) {
      await nameInput.fill('Test Config');
      console.log('Filled name');
    }

    // API Key input
    const apiKeyInput = await page.$('input[placeholder="sk-ant-..."], input[type="password"]');
    if (apiKeyInput) {
      // Use fill to replace content
      await apiKeyInput.fill('sk-test-key-123456789');
      console.log('Filled API key');
    }

    await page.screenshot({ path: 'test-results/06_filled_new_config.png', fullPage: true });

    // Submit
    const saveBtn = await page.$('button[type="submit"]:has-text("保存配置")');
    if (saveBtn) {
      await saveBtn.click();
      await page.waitForTimeout(3000);
    }

    await page.screenshot({ path: 'test-results/07_after_create.png', fullPage: true });

    // Re-query for config articles
    const newConfigArticles = await page.$$('article');
    console.log(`Found ${newConfigArticles.length} config articles after create`);
  }

  // Step 6: Find and click edit button
  console.log('Step 6: Looking for edit button');
  const editButtons = await page.$$('button:has-text("编辑")');
  console.log(`Found ${editButtons.length} edit buttons`);

  if (editButtons.length > 0) {
    console.log('Clicking edit button...');
    await editButtons[0].click();
    await page.waitForTimeout(1500);

    await page.screenshot({ path: 'test-results/08_after_edit_click.png', fullPage: true });

    // Verify: Check if form switched to edit mode
    const editTitle = await page.$('h2:has-text("编辑配置")');
    console.log(`Edit form title visible: ${!!editTitle}`);

    // Verify: Check if NewTaskDialog overlay is NOT present
    // NewTaskDialog would be a large overlay covering the screen
    const bodyContent = await page.$('body');
    const bodyHTML = await bodyContent?.innerHTML();

    // Check if there's a visible large overlay (NewTaskDialog-like)
    const largeOverlays = await page.$$('[class*="fixed"]:not([class*="hidden"])');
    let hasBlockingOverlay = false;
    for (const overlay of largeOverlays) {
      const box = await overlay.boundingBox();
      const isVisible = await overlay.isVisible();
      if (isVisible && box && box.height > 400 && box.width > 600) {
        // Check if it contains NewTaskDialog text or similar
        const text = await overlay.textContent();
        if (text && (text.includes('新建任务') || text.includes('New Task') || text.includes('模型选择'))) {
          hasBlockingOverlay = true;
          console.log(`Found blocking overlay with text: ${text.substring(0, 100)}`);
        }
      }
    }

    console.log(`NewTaskDialog blocking: ${hasBlockingOverlay}`);

    // Check that SettingsView is still visible and functional
    const settingsVisible = await page.$('h1:has-text("设置"), h1:has-text("模型配置")');
    console.log(`Settings view visible: ${!!settingsVisible}`);

    // Verify the form is in edit mode (has "编辑配置" title and "更新配置" button)
    const updateBtn = await page.$('button:has-text("更新配置")');
    console.log(`Update button visible: ${!!updateBtn}`);

    console.log('\n=== TEST RESULT ===');
    if (editTitle && !hasBlockingOverlay && updateBtn) {
      console.log('PASS: Edit button works correctly, NewTaskDialog does not block SettingsView');
    } else {
      console.log('FAIL: Issue detected');
      console.log(`  - Edit title: ${!!editTitle}`);
      console.log(`  - Blocking overlay: ${hasBlockingOverlay}`);
      console.log(`  - Update button: ${!!updateBtn}`);
    }

  } else {
    console.log('No edit button found');
    await page.screenshot({ path: 'test-results/08_no_edit_button.png', fullPage: true });
  }

  await browser.close();
  console.log('\nTest complete');
})();