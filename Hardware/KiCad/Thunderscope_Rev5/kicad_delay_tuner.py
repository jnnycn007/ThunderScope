import sys
import math
from kipy import KiCad
from kipy.util.units import to_mm

er_per_layer = [2.78,-1,3.66,3.66,-1,2.78]
via_prop_delay_ps = 23.09 # sqrt(Cvia * Lvia)
board_height_mm = 1.6
mm_per_m = 1000.0
s_per_ps = 1e-12
c = 2.998e8
delay_tolerance_ps = 1

def main():

    try:
        kicad = KiCad()
    except BaseException as e:
        print(f"Not connected to KiCad: {e}")
        exit()

    pad_delay_dict = {}

    if (len(sys.argv) == 5):
        vivado_io_csv_file = sys.argv[4]
        pad_delay_dict = get_pad_delays(vivado_io_csv_file)
    
    if (len(sys.argv) > 2):
        netclass = sys.argv[1]
        matching_type = sys.argv[2]
        length_tolerance,per_net_delay_dict = calc_net_delays(kicad,netclass,matching_type,pad_delay_dict)

    if (len(sys.argv) > 3):    
        user_write_prompt = input("Write Rules File (y/n): ")
        if (user_write_prompt == "y"):
            rules_file = sys.argv[3]
            write_rule_file(per_net_delay_dict,rules_file,length_tolerance)


def get_pad_delays(vivado_io_csv_file):
    
    pad_delay_dict = {}
    
    with open(vivado_io_csv_file) as f:
        for line in f:
            d = line.split(',')
            if (len(d) < 6 or d[1] == "Pin Number"):
                pass
            else:
                try:
                    pad_delay_dict[d[1]] = (float(d[4]) + float(d[5])) * 0.5
                except:
                    pad_delay_dict[d[1]] = -1
    
    #for x, y in pad_delay_dict.items():
    #    print(x, y)
    
    return pad_delay_dict


def calc_net_delays(kicad,netclass,matching_type,pad_delay_dict):

    # Step 1
    # Get the nets to calc the delays for - filter by netclass

    board = kicad.get_board()
    
    #TRICKSY - All nets are also "default"
    nets = board.get_nets([netclass + ",Default"]) 
    num_nets = len(nets)

    # Step 2
    # Get stackup information
    # We are just doing number of copper layers for now  
    # Later we can grab dielectric info and spacing, then calc Er
    # But for now user gives us effective Er vals
    
    layers = board.get_stackup().layers
    
    # Ugh, this is confusing naming
    # Sort by layers with layer.type == 1 (copper)
    # Then put layer.layer (actually the kicad internal layer number) in a list
    # We will need this internal layer number later, and list len is now # of copper layers
    cu_layer_ids = [layer.layer for layer in layers if layer.type == 1]
    num_cu_layers = len(cu_layer_ids)

    # Step 3
    # Make a dictionary with the nets as a key and a list as a value 
    # The list will have lengths per layer, number of vias, and pad delay
    # Python is giving me "TypeError: unhashable type: 'Net'" so we'll keep our own dictionary

    per_net_stats = [[0 for _ in range(num_cu_layers + 2)] for _ in range(num_nets)]
    
    # Step 4 
    # Get all the tracks of our nets, and increment per_net_stats as needed
    # Remember that KiCad reports length in nanometers!  

    tracks = board.get_tracks()

    for track in tracks:
        for net_index in range (num_nets):
            if (track.net == nets[net_index]):
                for layer_index in range (num_cu_layers):
                    if (track.layer == cu_layer_ids[layer_index]):          
                        per_net_stats[net_index][layer_index] += to_mm(track.length())

    # Step 5
    # Let's get the vias as well
    # We are just getting number of vias and assuming all are the same
    # Later we can calc prop delay from their physical parameters
    # But for now user will give us a one prop delay for all of them                   

    vias = board.get_vias()

    for via in vias:
        for net_index in range (num_nets):
            if (via.net == nets[net_index]):        
                per_net_stats[net_index][num_cu_layers] += 1
    
    # Step 6
    # Get the pad delay for each of our nets
    # Assuming no conflict between pad names for now (BGA to Non-BGA should be fine, but BGA to BGA is dicey)
    
    pads = board.get_pads()

    for pad in pads:
        for net_index in range (num_nets):
            if (pad.net == nets[net_index]):
                if (pad.number in pad_delay_dict.keys()):
                    per_net_stats[net_index][num_cu_layers + 1] = pad_delay_dict[pad.number]
                    #print (pad.number)
                    #print (pad_delay_dict[pad.number])
    
    # Step 7
    # Calc the ps_per_mm scaling factor
    
    ps_per_mm_by_layer = [0 for _ in range(num_cu_layers)]
    
    for layer_index in range (num_cu_layers):
        if (er_per_layer[layer_index] > 0):
            ps_per_mm_by_layer[layer_index] = (1.0/(c*mm_per_m*s_per_ps/math.sqrt(er_per_layer[layer_index])))

    # Step 8
    # Apply the scaling factors per layer and vias delays and add everything up per net
    # We can make a simple dictionary here
    
    per_net_delay_dict = {}
    
    for net_index in range (num_nets):
        
        per_net_length_kicad = 0
        per_net_length_equiv = 0
        per_net_delay = 0
        
        # Traces
        for layer_index in range (num_cu_layers):
            per_net_length_kicad += per_net_stats[net_index][layer_index]
            per_net_delay += per_net_stats[net_index][layer_index] * ps_per_mm_by_layer[layer_index]
        
        # Vias
        per_net_length_kicad += per_net_stats[net_index][num_cu_layers] * board_height_mm
        per_net_delay += per_net_stats[net_index][num_cu_layers] * via_prop_delay_ps    
        
        # Pad Delay
        per_net_delay += per_net_stats[net_index][num_cu_layers + 1]
        
        # Convert Net Delay to Length for Equiv
        per_net_length_equiv = per_net_delay / ps_per_mm_by_layer[0]

        # Dictionary
        per_net_delay_dict[nets[net_index].name] = [per_net_length_kicad,per_net_delay,per_net_length_equiv]
    
    # Step 9
    # Find net with greatest delay, then make use equivalent length deltas to set Length Rules  
    # For each net: Length Rule = Kicad Length + (Equiv Length of max delay net  - Equiv Length)
    delay_max = 0
    equiv_len_max = 0


    if (matching_type == "skew"):
        
        pair_counter = 2
        delay_max_list = []
        equiv_len_max_list = []
        
        for x in per_net_delay_dict.values():
            if (pair_counter > 0):
                if x[1] > delay_max:
                    delay_max = x[1]
                    equiv_len_max = x[2]
                pair_counter -= 1
            if (pair_counter == 0):
                delay_max_list.append(delay_max)
                equiv_len_max_list.append(equiv_len_max)
                pair_counter = 2
                delay_max = 0
                equiv_len_max = 0
        
        print(delay_max_list)

        pair_counter = 2
        net_counter = 0
        for x in per_net_delay_dict.values():
            if (pair_counter > 0):
                x.append(x[0] + equiv_len_max_list[net_counter] - x[2])
                x.append(x[1] - delay_max_list[net_counter])
                pair_counter -= 1
            if (pair_counter == 0):
                net_counter += 1
                pair_counter = 2            
        
    else: 
        for x in per_net_delay_dict.values():
            if x[1] > delay_max:
                delay_max = x[1]
                equiv_len_max = x[2]

        for x in per_net_delay_dict.values():
            x.append(x[0] + equiv_len_max - x[2])
            x.append(x[1] - delay_max)

    net_name_length = 0
    for x in per_net_delay_dict.keys():
        if len(x) > net_name_length:
            net_name_length = len(x)

    print ("-"*(101+net_name_length))
    
    print ("| Net" + " " * (net_name_length) \
           + "| Kicad Len (mm)   | Total Delay (ps) | Delay Diff (ps)  | Equiv Len (mm)   | Rule Len (mm)    |")
    
    print ("-"*(101+net_name_length))
    
    
    if (matching_type == "skew"):
        pair_counter = 2
        for x, y in per_net_delay_dict.items():
            pair_counter -= 1
            print("| " + x + " " * (net_name_length-len(x)+3) + "| " \
                + f'{y[0]:.3f}' + " " * (17 - len(f'{y[0]:.3f}')) + "| " \
                + f'{y[1]:.3f}' + " " * (17 - len(f'{y[1]:.3f}')) + "| " \
                + f'{y[4]:.3f}' + " " * (17 - len(f'{y[4]:.3f}')) + "| " \
                + f'{y[2]:.3f}' + " " * (17 - len(f'{y[2]:.3f}')) + "| " \
                + f'{y[3]:.3f}' + " " * (17 - len(f'{y[3]:.3f}')) + "|")
            if (pair_counter == 0):
                print ("-"*(101+net_name_length))
                pair_counter = 2  
    
    else:
        for x, y in per_net_delay_dict.items():
            print("| " + x + " " * (net_name_length-len(x)+3) + "| " \
                + f'{y[0]:.3f}' + " " * (17 - len(f'{y[0]:.3f}')) + "| " \
                + f'{y[1]:.3f}' + " " * (17 - len(f'{y[1]:.3f}')) + "| " \
                + f'{y[4]:.3f}' + " " * (17 - len(f'{y[4]:.3f}')) + "| " \
                + f'{y[2]:.3f}' + " " * (17 - len(f'{y[2]:.3f}')) + "| " \
                + f'{y[3]:.3f}' + " " * (17 - len(f'{y[3]:.3f}')) + "|")
        print ("-"*(101+net_name_length))

    length_tolerance = delay_tolerance_ps / ps_per_mm_by_layer[0]

    return length_tolerance,per_net_delay_dict


def write_rule_file(per_net_delay_dict,rules_file,length_tolerance):

    with open(rules_file) as f:
        lines = f.readlines()
    
    line_num_start = -1

    for x in range(len(lines)):
        if lines[x].startswith("# DELAY TUNER RULES"):
            line_num_start = x
    
    if (line_num_start != -1):
        with open(rules_file, "w") as f:
            for x in range(len(lines)):
                f.write(lines[x])
                if (x == line_num_start):
                    break
            f.write("\n")
            for x,y in per_net_delay_dict.items():
                f.write('(rule "' + x + ' length"\n')
                f.write('\t(condition "A.NetName == \'' + x + '\'")\n')
                f.write("\t(constraint length ")
                f.write("(min " +  str(y[3] - length_tolerance) + "mm) ")
                f.write("(opt " +  str(y[3]) + "mm) ")
                f.write("(max " +  str(y[3] + length_tolerance) + "mm)))\n")
                
if __name__ == '__main__':
    main()    