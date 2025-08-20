import time
import logging

from DrissionPage import ChromiumPage
from DrissionPage.items import ChromiumElement
from DrissionPage.errors import PageDisconnectedError, ContextLostError

logger = logging.getLogger(__name__)

class CloudflareBypasser:
    def __init__(self, driver: ChromiumPage, max_retries=-1):
        self.driver = driver
        self.max_retries = max_retries

    def search_recursively_shadow_root_with_iframe(self, ele: ChromiumElement):
        if ele.shadow_root:
            if ele.shadow_root.child().tag == "iframe":
                return ele.shadow_root.child()
        else:
            for child in ele.children():
                result = self.search_recursively_shadow_root_with_iframe(child)
                if result:
                    return result
        return None

    def search_recursively_shadow_root_with_cf_input(self, ele: ChromiumElement):
        if ele.shadow_root:
            if ele.shadow_root.ele("tag:input"):
                return ele.shadow_root.ele("tag:input")
        else:
            for child in ele.children():
                result = self.search_recursively_shadow_root_with_cf_input(child)
                if result:
                    return result
        return None
    
    def locate_cf_button(self):
        button = None
        eles = self.driver.eles("tag:input")
        ele: ChromiumElement
        for ele in eles:
            if "name" in ele.attrs.keys() and "type" in ele.attrs.keys():
                if "turnstile" in ele.attrs["name"] and ele.attrs["type"] == "hidden":
                    try:
                        parent = ele.parent()
                        if parent and parent.shadow_root:
                            child = parent.shadow_root.child()
                            if child:
                                body = child("tag:body")
                                if body and body.shadow_root:
                                    button = body.shadow_root("tag:input")
                                    if button:
                                        break
                    except Exception as e:
                        logger.debug(f"Error finding turnstile button: {e}")
                        continue
            
        if button:
            return button
        else:
            # If the button is not found, search it recursively
            logger.info("Basic search failed. Searching for button recursively.")
            ele = self.driver.ele("tag:body", )
            iframe = self.search_recursively_shadow_root_with_iframe(ele)
            if iframe:
                button = self.search_recursively_shadow_root_with_cf_input(iframe("tag:body"))
            else:
                logger.info("Iframe not found. Button search failed.")
            return button

    def click_verification_button(self):
        try:
            button = self.locate_cf_button()
            if button:
                logger.info("Verification button found. Attempting to click.")
                button.click()
            else:
                logger.info("Verification button not found.")

        except Exception as e:
            if isinstance(e, PageDisconnectedError):
                raise
            elif isinstance(e, ContextLostError):
                pass
            else:
                logger.error(f"Error clicking verification button: {type(e).__name__}: {e}")

    def is_bypassed(self):
        try:
            title = self.driver.title.lower()
            return "just a moment" not in title
        except Exception as e:
            if isinstance(e, PageDisconnectedError):
                raise
            logger.error(f"Error checking page title: {e}")
            return False

    def bypass(self):
        try_count = 0

        while not self.is_bypassed():
            if 0 < self.max_retries + 1 <= try_count:
                logger.info("Exceeded maximum retries. Bypass failed.")
                break

            logger.debug(f"Attempt {try_count + 1}: Verification page detected. Trying to bypass...")
            self.click_verification_button()

            try_count += 1
            time.sleep(2)

        if self.is_bypassed():
            logger.debug("Bypass successful.")
        else:
            logger.debug("Bypass failed.")
