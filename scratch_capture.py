import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width': 1280, 'height': 800})
        await page.goto('http://127.0.0.1:5055')
        await page.fill('#login-username', 'admin')
        await page.fill('#login-password', 'admin123')
        await page.click('#btn-login-submit')
        await page.wait_for_timeout(2500)
        
        views = ['dashboard', 'forecast', 'production', 'store', 'po', 'master_bom', 'reports']
        for v in views:
            await page.evaluate(f'switchView("{v}")')
            await page.wait_for_timeout(1500)
            await page.screenshot(path=f'scratch_{v}.png')
            print(f'Captured {v}')
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
