#!/usr/bin/env python3
"""
Test Browser
Starts a Chrome browser with MITM proxy configuration for manual testing.
No timeout limits, auto-close functionality, or automatic challenge solving.
"""

import argparse
import logging
import time

import appdirs
from DrissionPage import ChromiumPage, ChromiumOptions
from DrissionPage.errors import PageDisconnectedError, BrowserConnectError

from cloudflyer.instance import MITMAddon, DEFAULT_ARGUMENTS, HEADLESS_ARGUMENTS, DEFAULT_BROWSER_PATH, DEFAULT_CERT_PATH
from cloudflyer.mitm import MITMProxy
from cloudflyer.utils import get_free_port
from cloudflyer.server import ProxyConfig

logger = logging.getLogger(__name__)

class TestBrowser:
    """Test browser class that starts a Chrome browser with MITM proxy for manual testing, without timeout and automatic challenge solving features"""
    
    def __init__(
        self,
        arguments: list = None,
        browser_path: str = DEFAULT_BROWSER_PATH,
        certdir: str = str(DEFAULT_CERT_PATH),
        use_hazetunnel: bool = True,
        headless: bool = False,
        default_proxy: dict = None,
        allow_local_proxy: bool = False,
    ):
        self.browser_path = browser_path
        self.certdir = certdir
        self.headless = headless
        self.default_proxy_config = default_proxy
        self.allow_local_proxy = allow_local_proxy
        
        # Choose appropriate default arguments based on headless mode
        if arguments is None:
            self.arguments = HEADLESS_ARGUMENTS if headless else DEFAULT_ARGUMENTS
        else:
            self.arguments = arguments
            
        self.driver: ChromiumPage = None
        self.addon = MITMAddon()
        self.mitm_port = get_free_port()
        self.mitm = MITMProxy(
            port=self.mitm_port,
            certdir=self.certdir,
            addons=[self.addon],
            use_hazetunnel=use_hazetunnel,
            default_external_proxy=self.default_proxy_config,
        )
        
    def start(self, initial_url=None):
        """Start the browser"""
        print(f"Starting MITM proxy on port: {self.mitm_port}")
        
        # Start MITM proxy
        self.mitm.start()
        
        # Wait for MITM proxy to be ready
        for i in range(100):  # Wait up to 10 seconds
            if self.addon.ready.wait(timeout=0.1):
                break
            # If MITM thread dies, stop waiting
            if not self.mitm.thread.is_alive():
                raise RuntimeError("MITM proxy failed to start.")
        else:
            raise RuntimeError("MITM proxy failed to start.")
        
        print("MITM proxy started successfully")
        
        # Configure browser options
        options = ChromiumOptions().auto_port()
        options.set_paths(browser_path=self.browser_path)
        options.ignore_certificate_errors(True)
        
        if self.headless:
            options.headless(True)
            print("Headless mode enabled")
        else:
            print("GUI mode enabled")
            
        # Add arguments
        for argument in self.arguments:
            options.set_argument(argument)
            
        # Set proxy
        options.set_proxy(f"http://127.0.0.1:{self.mitm_port}")
        options.set_retry(20, 0.5)
        
        print("Starting browser...")
        
        # Check for BrowserConnectError and run debugger if needed
        try:
            logger.info("Checking DrissionPage browser connection...")
            self.driver = ChromiumPage(addr_or_opts=options, timeout=0.5)
        except BrowserConnectError as e:
            logger.warning(f"Browser connection failed: {e}")
            logger.info("Running DrissionPage debugger to diagnose the issue...")
            
            # Import and run debugger
            from cloudflyer.drission_debugger import run_drission_diagnosis
            diagnosis_result = run_drission_diagnosis(
                browser_path=self.browser_path,
                proxy_port=self.mitm_port,
                headless=self.headless
            )
            
            # Re-raise original error with diagnostic information
            raise BrowserConnectError(f"Browser connection failed. Diagnosis completed. Check logs for recommendations. Original error: {e}") from e
        
        print("Browser started successfully!")
        
        # Load initial page
        if initial_url:
            print(f"Loading initial URL: {initial_url}")
            self.navigate_to(initial_url)
        else:
            print("Loading initial page...")
            self.driver.get('https://internals.cloudflyer.com/index')
            print("Initial page loaded successfully")
        
        return self
        
    def stop(self):
        """Stop browser and proxy"""
        print("Closing browser...")
        try:
            if self.driver:
                self.driver.quit()
                self.driver = None
                print("Browser closed")
        except:
            print("Error occurred while closing browser")
            
        print("Closing MITM proxy...")
        if self.mitm:
            self.mitm.stop()
            self.mitm = None
            print("MITM proxy closed")
    
    def navigate_to(self, url: str):
        """Navigate to specified URL"""
        if not self.driver:
            raise RuntimeError("Browser not started, please call start() method first")
            
        print(f"Navigating to: {url}")
        # Ensure URL includes protocol
        if not url.startswith(("http://", "https://")):
            url = "http://" + url
            
        success = self.driver.get(url)
        if success:
            print(f"Successfully navigated to: {url}")
        else:
            print(f"Failed to navigate to: {url}")
        return success
    
    def get_current_url(self):
        """Get current URL"""
        if not self.driver:
            return None
        return self.driver.url
    
    def get_page_title(self):
        """Get page title"""
        if not self.driver:
            return None
        return self.driver.title
    
    def keep_alive(self):
        """Keep browser running until manually stopped"""
        print("Browser is running, press Ctrl+C to stop...")
        try:
            while True:
                time.sleep(1)
                # Check if browser is still running
                if self.driver:
                    try:
                        # Simple browser connection check
                        _ = self.driver.url
                    except (PageDisconnectedError, AttributeError):
                        print("Browser connection lost")
                        break
                else:
                    break
        except KeyboardInterrupt:
            print("\nInterrupt signal received, shutting down...")
        finally:
            self.stop()
    
    def run_once(self, url):
        """Run browser once in headless mode - navigate to URL, wait for load, display results, then exit"""
        print(f"Navigating to: {url}")
        
        # Navigate to URL
        success = self.navigate_to(url)
        if not success:
            print(f"Failed to navigate to: {url}")
            return False
        
        # Wait a bit for page to fully load
        print("Waiting for page to load...")
        time.sleep(3)
        
        # Display results
        print("\n=== Results ===")
        current_url = self.get_current_url()
        page_title = self.get_page_title()
        
        print(f"Final URL: {current_url}")
        print(f"Page Title: {page_title}")
        
        # Check for common indicators
        try:
            # Get some basic page info
            if self.driver:
                # Check if page seems loaded
                ready_state = self.driver.run_js("return document.readyState")
                print(f"Document Ready State: {ready_state}")
                
                # Check for common challenge indicators
                page_source = self.driver.html
                if "cloudflare" in page_source.lower():
                    print("Status: Cloudflare detected")
                elif "captcha" in page_source.lower():
                    print("Status: CAPTCHA detected") 
                elif "turnstile" in page_source.lower():
                    print("Status: Turnstile detected")
                else:
                    print("Status: Page loaded normally")
                
                # Show page size
                print(f"Page Size: {len(page_source)} characters")
                
        except Exception as e:
            print(f"Error getting page info: {e}")
        
        print("=== End Results ===\n")
        return True

def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="CloudFlyer Test Browser - Chrome browser with MITM proxy for manual testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python test_browser.py                                      # Start in GUI mode
  python test_browser.py --headless --url https://example.com # Headless mode (requires --url)
  python test_browser.py -x socks5://127.0.0.1:1080           # Use default proxy
  python test_browser.py --url https://example.com            # GUI mode with specific URL
  python test_browser.py --browser-path /path/to/chrome       # Use custom Chrome path
        """
    )
    
    parser.add_argument(
        '-x', '--default-proxy',
        help='Default upstream proxy (format: scheme://host:port or scheme://user:pass@host:port)'
    )
    
    parser.add_argument(
        '--headless',
        action='store_true',
        help='Run browser in headless mode (no GUI)'
    )
    
    parser.add_argument(
        '--url',
        default=None,
        help='Initial URL to navigate to (required in headless mode, optional in GUI mode)'
    )
    
    parser.add_argument(
        '--browser-path',
        default=DEFAULT_BROWSER_PATH,
        help=f'Path to Chrome/Chromium browser (default: {DEFAULT_BROWSER_PATH or "auto-detect"})'
    )
    
    parser.add_argument(
        '--cert-dir',
        default=str(DEFAULT_CERT_PATH),
        help=f'Directory for MITM certificates (default: {DEFAULT_CERT_PATH})'
    )
    
    parser.add_argument(
        '--no-hazetunnel',
        action='store_true',
        help='Disable hazetunnel functionality'
    )
    
    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default='INFO',
        help='Set logging level (default: INFO)'
    )
    
    parser.add_argument(
        '--arguments',
        nargs='*',
        help='Additional Chrome arguments to pass to the browser'
    )
    
    return parser.parse_args()

def main():
    """Main function"""
    # Parse command line arguments
    args = parse_arguments()
    
    # Setup logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("=== CloudFlyer Test Browser ===")
    print("This tool starts a Chrome browser with MITM proxy configuration")
    if args.headless:
        print("Running in headless mode - will navigate to URL and show results")
    else:
        print("for manual testing - no timeout limits, auto-close, or automatic challenge solving")
    print()
    
    # Check headless mode requirements
    if args.headless and not args.url:
        print("ERROR: --url is required when running in headless mode")
        print("Use: python test_browser.py --headless --url https://example.com")
        return
    
    # Parse default proxy if provided
    default_proxy_config = None
    if args.default_proxy:
        try:
            proxy_config = ProxyConfig.from_string(args.default_proxy)
            default_proxy_config = proxy_config.to_dict()
            print(f"Using default proxy: {args.default_proxy}")
        except ValueError as e:
            print(f"Error parsing default proxy: {e}")
            return
    
    # Prepare arguments
    browser_arguments = None
    if args.arguments:
        # Combine default arguments with user-provided ones
        default_args = HEADLESS_ARGUMENTS if args.headless else DEFAULT_ARGUMENTS
        browser_arguments = default_args + args.arguments
        print(f"Using custom arguments: {args.arguments}")
    
    # Create test browser instance
    browser = TestBrowser(
        arguments=browser_arguments,
        browser_path=args.browser_path,
        certdir=args.cert_dir,
        headless=args.headless,
        use_hazetunnel=not args.no_hazetunnel,
        default_proxy=default_proxy_config,
    )
    
    try:
        # Start browser
        browser.start(initial_url=args.url if not args.headless else None)
        
        if args.headless:
            # Headless mode: run once and exit
            print(f"Running in headless mode...")
            success = browser.run_once(args.url)
            if success:
                print("Headless mode completed successfully")
            else:
                print("Headless mode failed")
        else:
            # GUI mode: display info and keep running
            print(f"Current URL: {browser.get_current_url()}")
            print(f"Page title: {browser.get_page_title()}")
            print()
            
            # Keep running
            browser.keep_alive()
        
    except Exception as e:
        print(f"Error: {e}")
        logger.exception("Error occurred while starting test browser")
    finally:
        # Ensure resource cleanup
        try:
            browser.stop()
        except:
            pass

if __name__ == "__main__":
    main()
