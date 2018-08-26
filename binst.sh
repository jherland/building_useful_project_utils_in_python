#!/bin/bash
#
# Knows a little about this and that to speed up test software
# uploads.

self=`basename $0`
toplevel=`readlink -f "$(dirname "$0")/.."`

help=
listtargets=
reboot_after_install=
verbose=
cat_cmd=cat
nc_cmd=nc6
scp_cmd=scp
postbinstcmd=
subbinst=
installimageargs=
from_prod=
objdir=

show_prods() {
    cat <<EOF
asterix
asterix.apps
asterix.gui
asterix.prodtest
asterix.slaveapps
barents
barents.prodtest
carbon
carbon.gui
carbon.prodtest
ce-host
drishti
drishti.apps
drishti.gui
drishti.prodtest
halley
halley.apps
idefix
moody
pyramid
pyramid.prodtest
sunrise
sunrise.prodtest
tempo
zenith
zenith.prodtest
EOF
}

usage() {
    cat <<EOF
Standard binst using netcat for efficiency:
  $self [-f <file>] [-vy -4] [-e <args>] -t <product> <destination>

Via another host:
  $self --via <host> [-f <file>] [-vy] [-e <args>] -t <product> <destination>

List all available target types:
  $self --tlist

Full option list:
  -O|--objdir - Pick install file from this path
  -f|--file - Installs a specific image from the file specified.
  -v|--verbose - Show verbose file transfer information. Requires pv(1).
  -y|--allow-test-software - Allow installing test sw on release installs.
  -u|--unprod - Move from production release using remotesupport user.
  -e <args> - Add these extra arguments to installimage on target.
  -t <product> - Install image for this product type. May be repeated.
  <destination> - IP address or hostname of the target. (IPv4/IPv6/IPv6LL)
  --tlist|--list-targets - Show supported targets for binst.
  --via <host> - Install via another host.
  -4 Use nc instead of nc6
  -h|--help - Print this message and exit.
EOF
}

is_ipv6() {
    [[ "$*" =~ :: ]] && return 0
    coloncount=`echo $* | tr -cd : | wc -c`
    [ "$coloncount" -eq 7 ]
}

is_linklocal() {
    [[ "$*" =~ (^|@)fe80:: ]]
}

guess_interface() {
    ip -o route get "$1" | sed -nre '1s/.*dev (\w+).*/\1/p'
}

while [ -n "$1" ]; do
    case "$1" in
        --tlist|--list-targets)
            listtargets=1
            ;;
        -h|--help)
            help=1
            ;;
        -f|--file)
            shift
            if [ -z "$1" ]; then
                fail=1
                help=1
            fi
            userfile="$1"
            ;;
        -O|--objdir)
            shift
            if [ -z "$1" ]; then
                fail=1
                help=1
            fi
            objdir="$1"
            ;;
        -t)
            shift
            if [ -z "$1" ]; then
                fail=1
                help=1
            fi
            targetarray+=("$1")
            ;;
        -e|--installimage-args)
            shift
            if [ -z "$1" ]; then
                fail=1
                help=1
            fi
            installimageargs="$1"
            ;;
        -y|--allow-test-software)
            allow_test_software=1
            ;;
        -u|--unprod)
            from_prod=1
            ;;
        -v|--verbose)
            verbose=1
            # This option requires external pv command which could be installed using
            # yum or apt-get
            if [ "`command -v pv`" ] ;then
                cat_cmd=pv
            else
                echo 'pv(1) is not installed. Cannot be verbose during file transfer.'
            fi
            ;;
        -4)
            nc_cmd=nc
            ;;
        --via)
            shift
            if [ -z "$1" ]; then
                fail=1
                help=1
            fi
            via="$1"
            ;;
        *)
            if [ -n "$dest" ] ;then
                echo "Cannot specify multiple destinations ($dest and $1)"
                fail=1
                help=1
            fi
            dest="$1"
            ;;
    esac

    shift
done

if [ -n "${listtargets}" ]; then
    show_prods | sort
    exit 0
fi

if [ ${#targetarray[*]} -eq "0" ]; then
    help=1
    fail=1
fi

if [ "$userfile" -a ${#targetarray[*]} -ne "1" ]; then
    echo "-f option not supported for multiple targets"
    exit 0
fi

if [ -n "${help}" ]; then
    usage
    if [ -n "${fail}" ]; then
        exit 1
    else
        exit 0
    fi
fi

ssh_dest="$dest"
scp_dest="$dest"
if is_ipv6 $dest ;then
    if is_linklocal $dest && [[ ! "$dest" =~ % ]] ;then
        ssh_dest+="%`guess_interface $ssh_dest`"
    fi
    scp_dest="[$ssh_dest]"
fi

ssh_cmd=ssh
ssh_wrapper() {
    if [ "$via" ] ;then
        declare -a final_args
        for arg in "$@" ; do final_args+=("'$arg'") ; done
        ssh root@"$via" -- "ssh -o 'StrictHostKeyChecking=no' -o 'UserKnownHostsFile=/dev/null' ${final_args[@]}"
    else
        $ssh_cmd -o 'StrictHostKeyChecking=no' -o 'UserKnownHostsFile=/dev/null' "$@"
    fi
}

########### Find target ############
for target in "${targetarray[@]}"; do

    filter=
    finishcmd=
    file=
    echo "Installing ${target}"
    case "${target}" in
        asterix)
            desc="Asterix Complete Image, target arm linux"
            ;;
        asterix.apps)
            buildtarget="asterix /asterix/a8/apps"
            desc="Asterix arm-a8 application code, target arm linux"
            targetpath=/mnt/base/active/apps.img
            postbinstcmd="/bin/mainrestart update"
            ;;
        asterix.gui)
            buildtarget="asterix.gui"
            desc="Asterix GUI code, target arm linux"
            finaltargetpath=/mnt/base/active/gui.img
            targetpath="$finaltargetpath.tmp"
            postbinstcmd="/etc/init.d/S13gui unmount_img > /dev/null 2>&1 &&
                          mv $targetpath $finaltargetpath &&
                          /etc/init.d/S13gui mount_img > /dev/null 2>&1"
            ;;
        asterix.prodtest)
            desc="Asterix Prodtest Image, target arm linux"
            ;;
        asterix.slaveapps)
            buildtarget="asterix /asterix/slaveapps"
            desc="Asterix application code for slave processors, target arm-m3 and c674x, sysbios"
            targetpath=/mnt/base/active/slaveapps.img
            postbinstcmd="/bin/mainrestart update"
            ;;
        barents)
            desc="Barents Image, target arm linux"
            installimage[0]="/sbin/installimage"
            ;;
        barents.prodtest)
            desc="Barents Prodtest Image, target arm linux"
            installimage[0]="/sbin/installimage"
            ;;
        carbon)
            desc="Carbon Complete Image, target tilera linux"
            ;;
        carbon.gui)
            buildtarget="carbon.gui"
            desc="Carbon GUI code, target tilera linux"
            finaltargetpath=/mnt/base/active/fs/gui.img
            targetpath="$finaltargetpath.tmp"
            postbinstcmd="systemctl stop run-mnt-gui.mount &&
                          mv $targetpath $finaltargetpath &&
                          systemctl start gui.service"
            ;;
        carbon.prodtest)
            desc="Carbon Prodtest Image, target tilera linux"
            ;;
        ce-host)
            desc="CE Host VM Image, target x86 linux"
            installimage[0]="/sbin/installimage"
            ssh_cmd=vm_ssh
            ;;
        drishti)
            desc="Drishti Complete Image, target arm linux"
            ;;
        drishti.apps)
            buildtarget="drishti /drishti/a9/apps"
            desc="Drishti arm-a9 application code, target arm linux"
            targetpath=/mnt/base/active/apps.img
            ;;
        drishti.gui)
            buildtarget="drishti.gui"
            desc="Drishti GUI code, target arm linux"
            finaltargetpath=/mnt/base/active/gui.img
            targetpath="$finaltargetpath.tmp"
            postbinstcmd="/etc/init.d/S13gui unmount_img > /dev/null 2>&1 &&
                          mv $targetpath $finaltargetpath &&
                          /etc/init.d/S13gui mount_img > /dev/null 2>&1"
            ;;
        drishti.prodtest)
            desc="Drishti Prodtest Image, target arm linux"
            ;;
        drishti.qml2)
            desc="Drishti + QML2 Complete Image, target arm linux"
            ;;
        halley)
            desc="Halley Complete Image, target arm linux"
            ;;
        halley.apps)
            buildtarget="halley /halley/arm/apps"
            desc="Halley arm application code, target arm linux"
            targetpath=/mnt/base/active/apps.img
            ;;
        idefix)
            desc="Idefix Complete Image, target arm linux"
            ;;
        moody)
            desc="Moody Complete Image, target arm linux"
            ;;
        pyramid)
            desc="Pyramid complete image, target arm linux"
            ;;
        pyramid.prodtest)
            desc="Pyramid prodtest image, target arm linux"
            ;;
        sunrise|zenith)
            desc="$target Complete Image, target arm linux"
            installimage[0]="/sbin/installimage"
            installimage[1]="/vendor/sbin/installimage"
            ;;
        sunrise.gui)
            buildtarget="sunrise.gui"
            desc="Sunrise GUI code, target arm linux"
            finaltargetpath=/mnt/base/active/fs/gui.img
            targetpath="$finaltargetpath.tmp"
            postbinstcmd="touch /mnt/base/active/pkg.modified &&
                          systemctl stop run-mnt-gui.mount &&
                          mv $targetpath $finaltargetpath &&
                          systemctl start gui.service"
            ;;
        sunrise.prodtest|zenith.prodtest)
            desc="$target Prodtest Image, target arm linux"
            installimage[0]="/sbin/installimage"
            installimage[1]="/vendor/sbin/installimage"
            ;;
        tempo)
            desc="Tempo Complete Image, target arm linux"
            ;;
        *)
            echo "Unknown target \"$target\""
            exit 1
            ;;
    esac
    [ -z "${desc}" ] && \
        desc="No description for ${target}.  Consider configuring binst to have one."
    [ -z "${buildtarget}" ] && buildtarget="${target}"
    [ -z "${installimage}" ] && installimage="/sbin/installimage"

    if [ -z "$userfile" ]; then
        [ -n "$objdir" ] && oarg="-O $objdir"
        file="`${toplevel}/build/build $oarg -t ${buildtarget} --print-target-names -Q`"
        if [ $? != 0 ]; then
            echo "Failed to locate build product"
            exit 1
        fi
        if [ ${file:0:1} == "/" ]; then
            file=$file
        else
            file="${toplevel}/$file"
        fi
    else
        file="$userfile"
    fi

    if [ -z "${file}" ] || [ -z "${targetpath}" -a -n "${filter}" ]; then
    ########### No such luck ############
        echo ""
        echo "Hello, `whoami`"
        echo "${self} needs to be configured for ${target}."
        exit 1
    fi

    if [ -n "${filter}" ]; then
        oldfile="${file}"
        file="`mktemp`"
        case "${filter}" in
            bunzip2)
                bunzip2 -c "${oldfile}" > "${file}"
                ;;
            gunzip)
                gunzip -c "${oldfile}" > "${file}"
                ;;
            *)
                echo "Unknown filter '${filter}'"
                exit 1
                ;;
        esac
    else
        oldfile="${file}"
    fi

########### Go at it ############
    echo "${desc}"
    echo "File: ${oldfile}"
    echo "Destination: ${dest}"
    if [ -n "${via}" ] ; then
        echo "Via: ${via}"
    fi
    echo "Please Wait..."

    if [ -n "$subbinst" ]; then
        ${subbinst} ${target}
    elif [ -n "$from_prod" ]; then
        decode_url=http://rst.rd.tandberg.com/
        cat <<EOF

To go from production SW to test SW you need to create a "remotesupport" user.
This can be done at http://${dest}/web/recovery/remotesupportuser or using
    xcom // RemoteSupportUser Create
at the tsh and then decoding the phrase at $decode_url

EOF
        read -p "Do so now and hit enter when ready..."
        ${cat_cmd} ${file} | ssh_wrapper remotesupport@"${ssh_dest}" "touch /tmp/allow_test_software; . /etc/profile; sudo ${installimage} -k /mnt/base/active/rk -f - $installimageargs; [ -n \"${installimage[1]}\" ] && sudo ${installimage[1]} -k /mnt/base/active/rk -f - $installimageargs"
    elif [ -e "${file}" -o "${file}" = "-" ]; then
        if [ -n "$via" -a -n "${targetpath}" ] ; then
            echo "Option --via currently not supported together with a target using \"targetpath\""
            exit 1
        fi
        if [ -n "${allow_test_software}" ] ;then
            ssh_wrapper root@"${ssh_dest}" touch /tmp/allow_test_software
        fi
        if [ -z "${targetpath}" ] ; then
            ${cat_cmd} ${file} | ssh_wrapper root@"${ssh_dest}" ". /etc/profile; ${installimage} -k /mnt/base/active/rk -f - $installimageargs; [ -n \"${installimage[1]}\" ] && ${installimage[1]} -k /mnt/base/active/rk -f - $installimageargs"
        else
            ${scp_cmd} "${file}" "root@${scp_dest}:${targetpath}.tmp"
            ssh_wrapper root@"${ssh_dest}" "mv ${targetpath}.tmp ${targetpath}"
        fi
        [ "${oldfile}" != "${file}" ] && rm -f "${file}"
    else
        echo "image not found"
        exit 1
    fi

done

if [ -n "$postbinstcmd" ]; then
    ssh_wrapper -f root@"${ssh_dest}" ". /etc/profile; $postbinstcmd"
fi

exit 0
