"""
DrissionPage Browser Connection Debugger
Diagnoses ChromiumPage startup issues by testing different launch argument combinations to find working configurations
"""
import logging
import time
from typing import List, Dict, Optional, Tuple
from DrissionPage import ChromiumPage, ChromiumOptions
from DrissionPage.errors import BrowserConnectError

logger = logging.getLogger(__name__)

class DrissionDebugger:
    """DrissionPage connection issue debugger"""
    
    def __init__(self, browser_path: Optional[str] = None, proxy_port: Optional[int] = None):
        self.browser_path = browser_path
        self.proxy_port = proxy_port
        self.results = []
        
    def _create_minimal_options(self) -> ChromiumOptions:
        """Create minimal startup arguments"""
        options = ChromiumOptions().auto_port()
        if self.browser_path:
            options.set_paths(browser_path=self.browser_path)
        if self.proxy_port:
            options.set_proxy(f"http://127.0.0.1:{self.proxy_port}")
        return options
    
    def _create_default_options(self, headless: bool = False) -> ChromiumOptions:
        """Create default startup arguments (based on current configuration)"""
        # Import currently used arguments
        from .instance import HEADLESS_ARGUMENTS, DEFAULT_ARGUMENTS
        
        options = ChromiumOptions().auto_port()
        if self.browser_path:
            options.set_paths(browser_path=self.browser_path)
        options.ignore_certificate_errors(True)
        
        if headless:
            options.headless(True)
            arguments = HEADLESS_ARGUMENTS
        else:
            arguments = DEFAULT_ARGUMENTS
            
        for argument in arguments:
            options.set_argument(argument)
            
        if self.proxy_port:
            options.set_proxy(f"http://127.0.0.1:{self.proxy_port}")
            
        options.set_retry(20, 0.5)
        return options
    
    def _test_options(self, options: ChromiumOptions, description: str) -> Tuple[bool, Optional[str]]:
        """Test specified startup options"""
        driver = None
        try:
            logger.info(f"Testing: {description}")
            driver = ChromiumPage(addr_or_opts=options, timeout=2.0)
            
            # Simple test: access a basic page
            driver.get('data:text/html,<html><body><h1>Test</h1></body></html>')
            time.sleep(0.5)
            
            # Check if page loaded successfully
            if driver.title or "Test" in driver.html:
                logger.info(f"✓ {description} - Test successful")
                return True, None
            else:
                logger.warning(f"✗ {description} - Page load failed")
                return False, "Page load failed"
                
        except BrowserConnectError as e:
            error_msg = f"Browser connection error: {str(e)}"
            logger.warning(f"✗ {description} - {error_msg}")
            return False, error_msg
        except Exception as e:
            error_msg = f"Unknown error: {str(e)}"
            logger.warning(f"✗ {description} - {error_msg}")
            return False, error_msg
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
    
    def _binary_search_arguments(self, base_options: ChromiumOptions, arguments: List[str], headless: bool = False) -> List[str]:
        """Use binary search to find problematic startup arguments"""
        logger.info("Starting binary search test for startup arguments...")
        
        working_args = []
        problematic_args = []
        
        def test_args_subset(test_args: List[str]) -> bool:
            """Test argument subset"""
            options = ChromiumOptions().auto_port()
            if self.browser_path:
                options.set_paths(browser_path=self.browser_path)
            options.ignore_certificate_errors(True)
            
            if headless:
                options.headless(True)
                
            for arg in test_args:
                options.set_argument(arg)
                
            if self.proxy_port:
                options.set_proxy(f"http://127.0.0.1:{self.proxy_port}")
                
            success, _ = self._test_options(options, f"Testing args: {test_args[:3]}{'...' if len(test_args) > 3 else ''}")
            return success
        
        # Recursive binary search test
        def binary_test(args_to_test: List[str]):
            if not args_to_test:
                return
                
            if len(args_to_test) == 1:
                if test_args_subset(working_args + args_to_test):
                    working_args.extend(args_to_test)
                    logger.info(f"✓ Argument '{args_to_test[0]}' works fine")
                else:
                    problematic_args.extend(args_to_test)
                    logger.warning(f"✗ Argument '{args_to_test[0]}' causes problems")
                return
            
            # Split into two halves
            mid = len(args_to_test) // 2
            first_half = args_to_test[:mid]
            second_half = args_to_test[mid:]
            
            # Test first half
            if test_args_subset(working_args + first_half):
                working_args.extend(first_half)
                logger.info(f"✓ First half arguments work fine ({len(first_half)} args)")
            else:
                logger.info(f"First half arguments have issues, subdividing...")
                binary_test(first_half)
            
            # Test second half
            if test_args_subset(working_args + second_half):
                working_args.extend(second_half)
                logger.info(f"✓ Second half arguments work fine ({len(second_half)} args)")
            else:
                logger.info(f"Second half arguments have issues, subdividing...")
                binary_test(second_half)
        
        binary_test(arguments)
        
        return working_args, problematic_args
    
    def run_diagnosis(self, headless: bool = False) -> Dict:
        """Run complete diagnosis workflow"""
        logger.info("=" * 60)
        logger.info("Starting DrissionPage browser connection diagnosis")
        logger.info("=" * 60)
        
        diagnosis_result = {
            "minimal_test": None,
            "default_test": None,
            "binary_search_result": None,
            "recommendations": []
        }
        
        # 1. Test minimal startup arguments
        logger.info("\n1. Testing minimal startup arguments")
        logger.info("-" * 30)
        minimal_options = self._create_minimal_options()
        minimal_success, minimal_error = self._test_options(minimal_options, "Minimal startup arguments")
        diagnosis_result["minimal_test"] = {
            "success": minimal_success,
            "error": minimal_error
        }
        
        # 2. Test default startup arguments
        logger.info("\n2. Testing default startup arguments")
        logger.info("-" * 30)
        default_options = self._create_default_options(headless=headless)
        default_success, default_error = self._test_options(default_options, f"Default startup arguments ({'headless' if headless else 'non-headless'})")
        diagnosis_result["default_test"] = {
            "success": default_success,
            "error": default_error
        }
        
        # 3. If minimal works but default doesn't, perform binary search test
        if minimal_success and not default_success:
            logger.info("\n3. Minimal arguments work but default arguments fail, starting binary search test")
            logger.info("-" * 50)
            
            from .instance import HEADLESS_ARGUMENTS, DEFAULT_ARGUMENTS
            arguments = HEADLESS_ARGUMENTS if headless else DEFAULT_ARGUMENTS
            
            working_args, problematic_args = self._binary_search_arguments(
                minimal_options, arguments, headless=headless
            )
            
            diagnosis_result["binary_search_result"] = {
                "working_arguments": working_args,
                "problematic_arguments": problematic_args
            }
            
            # Test recommended configuration
            if working_args:
                logger.info("\nTesting recommended configuration...")
                recommended_options = ChromiumOptions().auto_port()
                if self.browser_path:
                    recommended_options.set_paths(browser_path=self.browser_path)
                recommended_options.ignore_certificate_errors(True)
                
                if headless:
                    recommended_options.headless(True)
                    
                for arg in working_args:
                    recommended_options.set_argument(arg)
                    
                if self.proxy_port:
                    recommended_options.set_proxy(f"http://127.0.0.1:{self.proxy_port}")
                    
                recommended_options.set_retry(20, 0.5)
                rec_success, rec_error = self._test_options(recommended_options, "Recommended configuration")
                
                if rec_success:
                    diagnosis_result["recommendations"].append({
                        "type": "working_arguments",
                        "description": "Use filtered working arguments",
                        "arguments": working_args
                    })
        
        # Generate recommendations
        self._generate_recommendations(diagnosis_result)
        
        return diagnosis_result
    
    def _generate_recommendations(self, result: Dict):
        """Generate diagnostic recommendations"""
        logger.info("\n" + "=" * 60)
        logger.info("Diagnosis Report")
        logger.info("=" * 60)
        
        minimal = result["minimal_test"]
        default = result["default_test"]
        binary = result.get("binary_search_result")
        
        if minimal["success"] and default["success"]:
            logger.info("✓ All tests passed, DrissionPage configuration is normal")
            result["recommendations"].append({
                "type": "success",
                "description": "Current configuration works fine, no modification needed"
            })
            
        elif not minimal["success"]:
            logger.error("✗ Even minimal configuration cannot work, possible issues:")
            logger.error(f"  - Error message: {minimal['error']}")
            logger.error("  - Chrome/Chromium not correctly installed or wrong path")
            logger.error("  - System permission issues")
            logger.error("  - Proxy configuration issues")
            
            result["recommendations"].extend([
                {
                    "type": "check_browser",
                    "description": "Check Chrome/Chromium installation and path settings"
                },
                {
                    "type": "check_permissions", 
                    "description": "Check system permissions and security software settings"
                },
                {
                    "type": "check_proxy",
                    "description": "Check if proxy configuration is correct"
                }
            ])
            
        elif minimal["success"] and not default["success"]:
            logger.warning("⚠ Minimal configuration works but default configuration fails")
            logger.warning(f"  - Default configuration error: {default['error']}")
            
            if binary and binary["problematic_arguments"]:
                logger.warning("  - Problematic startup arguments:")
                for arg in binary["problematic_arguments"]:
                    logger.warning(f"    * {arg}")
                    
                if binary["working_arguments"]:
                    logger.info("  - Available startup arguments:")
                    for arg in binary["working_arguments"]:
                        logger.info(f"    * {arg}")
                        
                    result["recommendations"].append({
                        "type": "use_filtered_args",
                        "description": "Use filtered startup arguments",
                        "arguments": binary["working_arguments"]
                    })
            
            result["recommendations"].append({
                "type": "use_minimal",
                "description": "Use minimal configuration temporarily"
            })
        
        # Output final recommendations
        if result["recommendations"]:
            logger.info("\nRecommended solutions:")
            for i, rec in enumerate(result["recommendations"], 1):
                logger.info(f"{i}. {rec['description']}")
                if "arguments" in rec:
                    logger.info(f"   Arguments: {rec['arguments']}")

def run_drission_diagnosis(browser_path: Optional[str] = None, proxy_port: Optional[int] = None, headless: bool = False) -> Dict:
    """Convenient function to run DrissionPage diagnosis"""
    debugger = DrissionDebugger(browser_path=browser_path, proxy_port=proxy_port)
    return debugger.run_diagnosis(headless=headless)
