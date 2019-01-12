import Database.config as config
import Google.interface as Google

def get_workbook():
    return Google.connect().open(config.WORKBOOK_NAME)