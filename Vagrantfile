# -*- mode: ruby -*-
# vi: set ft=ruby :

VAGRANTFILE_API_VERSION = "2"

Vagrant.configure(VAGRANTFILE_API_VERSION) do |config|

  config.vm.box = "http://people.mozilla.org/~ashort/zamboni-ubuntu.box"
  config.vm.network "forwarded_port", guest: 8000, host: 8000

  config.vm.provider "virtualbox" do |vb|
  #   vb.gui = false
    # Use VBoxManage to customize the VM. For example to change memory:
  #  vb.customize ["modifyvm", :id, "--memory", "512"]
  end

    config.vm.provision :puppet do |puppet|
        puppet.manifests_path = "vagrant/manifests"
        puppet.manifest_file  = "vagrant.pp"
    end
end
