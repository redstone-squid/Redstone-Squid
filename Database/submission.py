from datetime import datetime
import Database.config as config

class Submission:
    def __init__(self):
        self.id = None
        self.last_updated = None
        self.base_category = None
        self.door_width = None
        self.door_height = None
        self.door_pattern = None
        self.door_type = None
        self.fo_restrictions = None
        self.so_restrictions = None
        self.information = None
        self.build_width = None
        self.build_height = None
        self.build_depth = None
        self.relative_close_time = None
        self.relative_open_time = None
        self.absolute_close_time = None
        self.absolute_open_time = None
        self.build_date = None
        self.creators = None
        self.locational = None
        self.directional = None
        self.versions = None
        self.image_url = None
        self.youtube_link = None
        self.world_download_link = None
        self.server_ip = None
        self.coordinates = None
        self.command = None
        self.submitted_by = None
    
    def get_title(self):
        # Catagory
        title = self.base_category + ' '
        
        # Door dimensions
        if self.door_width and self.door_height:
            title += '{}x{} '.format(self.door_width, self.door_height)
        elif self.door_width:
            title += '{} Wide '.format(self.door_width)
        elif self.door_height:
            title += '{} High '.format(self.door_height)
        
        # First order restrictions
        if self.fo_restrictions != None:
            for restriction in self.fo_restrictions:
                if restriction != 'None':
                    title += '{} '.format(restriction)
        
        # Pattern
        if self.door_pattern[0] != 'Regular':
            for pattern in self.door_pattern:
                title += '{} '.format(pattern)

        # Door type
        if self.door_type == None:
            title += 'Door.'
        elif self.door_type == 'SKY':
            title += 'Skydoor.'
        elif self.door_type == 'TRAP':
            title += 'Trapdoor.'
        
        return title

    def get_description(self):
        description = []

        # Second order restrictions
        if self.so_restrictions != None and self.so_restrictions[0] != 'None':
            description.append(', '.join(self.so_restrictions))
            
        if not config.VERSIONS_LIST[-1] in self.versions:
            description.append('**Broken** in current version.')
        
        if self.locational == 'LOCATIONAL':
            description.append('**Locational**.')
        elif self.locational == 'LOCATIONAL_FIX':
            description.append('**Locational** with known fixes for each location.')
        
        if self.directional == 'DIRECTIONAL':
            description.append('**Directional**.')
        elif self.directional == 'DIRECTIONAL_FIX':
            description.append('**Directional** with known fixes for each direction.')

        if self.information:
            description.append('\n' + str(self.information))
        
        if len(description) > 0:
            return '\n'.join(description)
        else:
            return None

    def get_versions_string(self):

        versions = []

        linking = False
        first_version = None
        last_version = None

        for index, version in enumerate(config.VERSIONS_LIST):
            if version in self.versions:
                
                if linking == False:
                    linking = True
                    first_version = version
                    last_version = version
                else:
                    last_version = version

            elif linking == True:
                linking = False

                if first_version == last_version:
                    versions.append(first_version)
                else:
                    versions.append('{} - {}'.format(first_version, last_version))

                first_version = None
                last_version = None

            if index == len(config.VERSIONS_LIST) - 1 and linking == True:
                if first_version == last_version:
                    versions.append(first_version)
                else:
                    versions.append('{} - {}'.format(first_version, last_version))
        
        return ', '.join(versions)


    def get_meta_fields(self):
        fields = {}

        fields['Dimensions'] = '{}x{}x{}'.format(self.build_width, self.build_height, self.build_depth)
        fields['Volume'] = str(self.build_width * self.build_height * self.build_depth)
        fields['Opening Time'] = str(self.relative_open_time)
        fields['Closing Time'] = str(self.relative_close_time)

        if self.absolute_open_time and self.absolute_close_time:
            fields['Absolute Opening Time'] = self.absolute_open_time
            fields['Absolute Closing Time'] = self.absolute_close_time

        fields['Creators'] = ', '.join(sorted(self.creators))
        fields['Date Of Completion'] = str(self.build_date)
        fields['Versions'] = self.get_versions_string()
        
        if self.server_ip:
            fields['Server'] = self.server_ip
            
            if self.coordinates:
                fields['Coordinates'] = self.coordinates

            if self.command:
                fields['Command'] = self.command

        if self.world_download_link:
            fields['World Download'] = self.world_download_link
        if self.youtube_link:
            fields['Video'] = self.youtube_link
        
        return fields
        
    @staticmethod
    def from_dict(dict):
        result = Submission()

        result.id = int(dict['Submission ID'])
        result.last_updated = datetime.strptime(dict['Last Update'], r'%d-%m-%Y %H:%M:%S')
        result.base_category = dict['Record Category']
        if dict['Door Width']:
            result.door_width = int(dict['Door Width'])
        if dict['Door Height']:
            result.door_height = int(dict['Door Height'])
        result.door_pattern = dict['Pattern'].split(', ')
        if dict['Door Type'] == 'Trapdoor':
            result.door_type = 'TRAP'
        if dict['Door Type'] == 'Skydoor':
            result.door_type = 'SKY'
        if dict['First Order Restrictions']:
            result.fo_restrictions = dict['First Order Restrictions'].split(', ')
        if dict['Second Order Restrictions']:
            result.so_restrictions = dict['Second Order Restrictions'].split(', ')
        if dict['Information About Build']:
            result.information = dict['Information About Build']
        result.build_width = int(dict['Width Of Build'])
        result.build_height = int(dict['Height Of Build'])
        result.build_depth = int(dict['Depth Of Build'])
        result.relative_close_time = float(dict['Relative Closing Time'])
        result.relative_open_time = float(dict['Relative Opening Time'])
        if dict['Absolute Closing Time']:
            result.absolute_close_time = float(dict['Absolute Closing Time'])
        if dict['Absolute Opening Time']:
            result.absolute_open_time = float(dict['Absolute Opening Time'])
        if dict['Date Of Creation']:
            result.build_date = dict['Date Of Creation']
        else:
            result.build_date = dict['Timestamp']
        result.creators = dict['In Game Name(s) Of Creator(s)'].split(',')
        if dict['Locationality'] == 'Locational with known fixes for each location':
            result.locational = 'LOCATIONAL_FIX'
        elif dict['Locationality'] == 'Locational without known fixes for each location':
            result.locational = 'LOCATIONAL'
        if dict['Directionality'] == 'Directional with known fixes for each direction':
            result.directional = 'DIRECTIONAL_FIX'
        elif dict['Directionality'] == 'Directional without known fixes for each direction':
            result.directional = 'DIRECTIONAL'
        result.versions = str(dict['Versions Which Submission Works In']).split(', ')
        if dict['Link To Image']:
            result.image_url = dict['Link To Image']
        if dict['Link To YouTube Video']:
            result.youtube_link = dict['Link To YouTube Video']
        if dict['Link To World Download']:
            result.world_download_link = dict['Link To World Download']
        if dict['Server IP']:
            result.server_ip = dict['Server IP']
        if dict['Coordinates']:
            result.coordinates = dict['Coordinates']
        if dict['Command To Get To Build/Plot']:
            result.command = dict['Command To Get To Build/Plot']
        result.submitted_by = dict['Your IGN / Discord Handle']

        return result

    def to_string(self):
        string = ''

        string += 'ID: {}\n'.format(self.id)
        string += 'Base Catagory: {}\n'.format(self.base_category)
        if self.door_width:
            string += 'Door Width: {}\n'.format(self.door_width)
        if self.door_height:
            string += 'Door Height: {}\n'.format(self.door_height)
        string += 'Pattern: {}\n'.format(' '.join(self.door_pattern))
        string += 'Door Type: {}\n'.format(self.door_type)
        if self.fo_restrictions:
            string += 'First Order Restrictions: {}\n'.format(', '.join(self.fo_restrictions))
        if self.so_restrictions:
            string += 'Second Order Restrictions: {}\n'.format(', '.join(self.so_restrictions))
        if self.information:
            string += 'Information: {}\n'.format(self.information)
        string += 'Build Width: {}\n'.format(self.build_width)
        string += 'Build Height: {}\n'.format(self.build_height)
        string += 'Build Depth: {}\n'.format(self.build_depth)
        string += 'Relative Closing Time: {}\n'.format(self.relative_close_time)
        string += 'Relative Opening Time: {}\n'.format(self.relative_open_time)
        if self.absolute_close_time:
            string += 'Absolute Closing Time: {}\n'.format(self.absolute_close_time)
        if self.absolute_open_time:
            string += 'Absolute Opening Time: {}\n'.format(self.absolute_open_time)
        string += 'Date Of Creation: {}\n'.format(self.build_date)
        string += 'Creators: {}\n'.format(', '.join(self.creators))
        if self.locational:
            string += 'Locationality Tag: {}\n'.format(self.locational)
        if self.directional:
            string += 'Directionality Tag: {}\n'.format(self.directional)
        string += 'Versions: {}\n'.format(', '.join(self.versions))
        if self.image_url:
            string += 'Image URL: {}\n'.format(self.image_url)
        if self.youtube_link:
            string += 'YouTube Link: {}\n'.format(self.youtube_link)
        if self.world_download_link:
            string += 'World Download: {}\n'.format(self.world_download_link)
        if self.server_ip:
            string += 'Server IP: {}\n'.format(self.server_ip)
        if self.coordinates:
            string += 'Coordinates: {}\n'.format(self.coordinates)
        if self.command:
            string += 'Command: {}\n'.format(self.command)
        string += 'Submitted By: {}\n'.format(self.submitted_by)
        
        return string