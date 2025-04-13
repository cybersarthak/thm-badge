import re
import os
import asyncio
import time
from playwright.async_api import async_playwright
from PIL import Image, ImageChops

# Your TryHackMe iframe string
TRYHACKME_IFRAME = '<iframe src="https://tryhackme.com/api/v2/badges/public-profile?userPublicId=2543434" style="border:none;"></iframe>'

def extract_url_from_iframe(iframe_string):
    url_match = re.search(r'src="([^"]+)"', iframe_string)
    if url_match:
        return url_match.group(1)
    else:
        raise ValueError("Could not extract URL from iframe string")

def extract_dimensions_from_iframe(iframe_string):
    width_match = re.search(r'width="([^"]+)"', iframe_string)
    height_match = re.search(r'height="([^"]+)"', iframe_string)
    
    width = int(width_match.group(1)) if width_match else 350
    height = int(height_match.group(1)) if height_match else 170
    
    return width, height

def crop_badge(image_path):
    try:
        img = Image.open(image_path)
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        
        width, height = img.size
        pixel_data = img.load()
        
        min_y = height
        max_y = 0
        
        bg_samples = [
            img.getpixel((5, 5)),
            img.getpixel((width-5, 5)),
            img.getpixel((5, height-5)),
            img.getpixel((width-5, height-5))
        ]
        
        similarity_threshold = 40
        
        def is_similar_to_background(pixel):
            r, g, b, a = pixel
            for bg_r, bg_g, bg_b, bg_a in bg_samples:
                if (abs(r - bg_r) + abs(g - bg_g) + abs(b - bg_b)) < similarity_threshold:
                    return True
            return False
        
        for y in range(height):
            row_has_content = False
            for x in range(width):
                pixel = pixel_data[x, y]
                if not is_similar_to_background(pixel):
                    row_has_content = True
                    break
            if row_has_content:
                min_y = min(min_y, y)
                max_y = max(max_y, y)
        
        padding = 5
        min_y = max(0, min_y - padding)
        max_y = min(height - 1, max_y + padding)
        
        if min_y >= max_y:
            print("No significant content found to crop, using full image")
            return image_path
        
        cropped = img.crop((0, min_y, width, max_y + 1))
        
        cropped_width, cropped_height = cropped.size
        cropped_pixels = cropped.load()
        
        edges = [(x, 0) for x in range(cropped_width)] + [(x, cropped_height-1) for x in range(cropped_width)]
        edges += [(0, y) for y in range(1, cropped_height-1)] + [(cropped_width-1, y) for y in range(1, cropped_height-1)]
        
        visited = set()
        while edges:
            x, y = edges.pop(0)
            if (x, y) in visited or x < 0 or y < 0 or x >= cropped_width or y >= cropped_height:
                continue
            visited.add((x, y))
            pixel = cropped_pixels[x, y]
            if is_similar_to_background(pixel):
                r, g, b, a = pixel
                cropped_pixels[x, y] = (r, g, b, 0)
                edges.extend([(x+1, y), (x-1, y), (x, y+1), (x, y-1)])
        
        cropped.save(image_path)
        print(f"Image cropped to badge content and edges made transparent: {os.path.abspath(image_path)}")
        return image_path
    
    except Exception as e:
        print(f"Error in crop_badge: {e}")
        return image_path

async def iframe_to_image_async(iframe_string, output_path="tryhackme_badge.png", wait_time=5000):
    url = extract_url_from_iframe(iframe_string)
    print(f"Extracted URL: {url}")
    
    width, height = extract_dimensions_from_iframe(iframe_string)
    print(f"Using dimensions: {width}x{height}")
    
    output_path = os.path.splitext(output_path)[0] + '.png'

    # Delete existing image to ensure fresh output
    if os.path.exists(output_path):
        print(f"Deleting existing image at {output_path}")
        os.remove(output_path)
    
    async with async_playwright() as p:
        print("Launching browser...")
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--no-first-run',
                '--no-zygote',
                '--disable-gpu',
                '--disable-background-timer-throttling',
                '--disable-backgrounding-occluded-windows',
                '--disable-background-networking'
            ]
        )
        context = await browser.new_context(viewport={'width': width, 'height': height}, device_scale_factor=2)
        page = await context.new_page()
        
        try:
            print(f"Navigating to URL: {url}")
            response = await page.goto(url, wait_until="networkidle", timeout=30000)
            
            if not response.ok:
                print(f"Failed to load page: {response.status} {response.status_text}")
                await page.screenshot(path=output_path)
                return output_path
            
            print(f"Waiting {wait_time}ms for content to load...")
            await page.wait_for_timeout(wait_time)
            await page.wait_for_load_state("networkidle")
            print(f"Page title: {await page.title()}")
            
            content_box = await page.evaluate("""() => {
                const elements = Array.from(document.querySelectorAll('*'))
                    .filter(el => el.offsetWidth > 0 && el.offsetHeight > 0 
                            && (el.textContent?.trim().length > 0 || el.querySelector('img')));
                elements.sort((a, b) => (b.textContent?.length || 0) - (a.textContent?.length || 0));
                const mainEl = elements.find(el => el.offsetWidth > 300 && el.offsetHeight > 50);
                if (mainEl) {
                    const rect = mainEl.getBoundingClientRect();
                    return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
                }
                return null;
            }""")
            
            if not content_box:
                print("No specific badge container found, taking full page screenshot")
                await page.screenshot(path=output_path)
            else:
                print(f"Found badge container at: {content_box}")
                padding = 5
                await page.screenshot(
                    path=output_path,
                    clip={
                        'x': max(0, content_box['x'] - padding),
                        'y': max(0, content_box['y'] - padding),
                        'width': content_box['width'] + (padding * 2),
                        'height': content_box['height'] + (padding * 2)
                    }
                )
            
            processed_path = crop_badge(output_path)
            print(f"Badge image saved to: {processed_path}")
            return processed_path
            
        except Exception as e:
            print(f"Error capturing screenshot: {e}")
            try:
                await page.screenshot(path=output_path)
                print(f"Fallback screenshot saved to: {output_path}")
            except Exception as inner_e:
                print(f"Failed to save fallback screenshot: {inner_e}")
            return output_path
        finally:
            await browser.close()

def iframe_to_image(iframe_string, output_path="tryhackme_badge.png", wait_time=5):
    return asyncio.run(iframe_to_image_async(
        iframe_string,
        output_path,
        wait_time * 1000
    ))

if __name__ == "__main__":
    print("Starting TryHackMe badge generation script...")
    time.sleep(2)
    
    image_path = iframe_to_image(TRYHACKME_IFRAME, "tryhackme_badge.png")

    if not os.path.exists(image_path):
        raise FileNotFoundError("ERROR: Badge image was not created.")
    
    size = os.path.getsize(image_path)
    print(f"\nBadge image saved at: {os.path.abspath(image_path)}")
    print(f"File size: {size} bytes")
    if size < 1000:
        print("WARNING: Image file size is very small, may indicate an issue")
    
    badge_url = extract_url_from_iframe(TRYHACKME_IFRAME)
    print("\nAdd this to your README.md:")
    print(f"""<a href="https://tryhackme.com/p/sarthakkk">
  <img src="tryhackme_badge.png" alt="TryHackMe Badge" style="width: 50%;">
</a>""")
